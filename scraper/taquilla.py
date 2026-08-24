import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.taquilla.com"
LIST_URL = f"{BASE_URL}/zaragoza"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "events.json"

DEFAULT_TTL_SECONDS = 60 * 60  # 1h
_CACHE_SCHEMA_VERSION = 4


_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _parse_date_ddmmyyyy(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    # Esperado: dd-mm-YYYY
    try:
        return datetime.strptime(s, "%d-%m-%Y").date()
    except ValueError:
        return None


def _extract_card_dates_and_price(text: str):
    # Formato habitual:
    # - "Del 20-03-2026 al 11-07-2026"
    # - "El 09-04-2026"
    range_m = re.search(r"Del\s+(\d{2}-\d{2}-\d{4})\s+al\s+(\d{2}-\d{2}-\d{4})", text)
    if range_m:
        d1 = _parse_date_ddmmyyyy(range_m.group(1))
        d2 = _parse_date_ddmmyyyy(range_m.group(2))
        if d1 and d2:
            # Precio mínimo: "Desde 15,00€"
            price_m = re.search(r"Desde\s*([0-9.,]+)\s*€", text)
            price_text = f"Desde {price_m.group(1)}€" if price_m else None
            price_min_eur: Optional[float]
            if price_m:
                raw = price_m.group(1)
                # Normaliza ES: 1.234,56 -> 1234.56
                normalized = raw.replace(".", "").replace(",", ".")
                try:
                    price_min_eur = float(normalized)
                except ValueError:
                    price_min_eur = None
            else:
                price_min_eur = None
            return d1, d2, price_text, price_min_eur

    single_m = re.search(r"\bEl\s+(\d{2}-\d{2}-\d{4})\b", text)
    if single_m:
        d = _parse_date_ddmmyyyy(single_m.group(1))
        if d:
            price_m = re.search(r"Desde\s*([0-9.,]+)\s*€", text)
            price_text = f"Desde {price_m.group(1)}€" if price_m else None
            price_min_eur: Optional[float]
            if price_m:
                raw = price_m.group(1)
                normalized = raw.replace(".", "").replace(",", ".")
                try:
                    price_min_eur = float(normalized)
                except ValueError:
                    price_min_eur = None
            else:
                price_min_eur = None
            return d, d, price_text, price_min_eur

    return None, None, None, None


def _slugify_category(name: str) -> str:
    # Simplificar para usarlo como valor de query string
    name = name.lower().strip()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
    }
    for k, v in replacements.items():
        name = name.replace(k, v)
    name = re.sub(r"[^a-z0-9\\-]", "", name)
    name = re.sub(r"\\-+", "-", name).strip("-")
    return name


def _card_to_event(card: Any, category_name: str) -> Optional[Dict[str, Any]]:
    # card: div.d-mosaic__box[data-link]
    title_el = card.find("h3")
    title = title_el.get_text(" ", strip=True) if title_el else None
    detail_rel = card.get("data-link")
    if not title or not detail_rel:
        return None

    detail_url = detail_rel
    if detail_url.startswith("/"):
        detail_url = f"{BASE_URL}{detail_url}"

    text = card.get_text(" ", strip=True)
    date_from, date_to, price_text, price_min_eur = _extract_card_dates_and_price(text)
    if not date_from or not date_to:
        return None

    category_slug = _slugify_category(category_name)
    return {
        "title": title,
        "category": category_name,
        "category_slug": category_slug,
        "date_from": date_from,
        "date_to": date_to,
        "time_text": None,
        "price_text": price_text,
        "price_min_eur": price_min_eur,
        "detail_url": detail_url,
    }


def _extract_zaragoza_occurrences(detail_html: str) -> List[Dict[str, Any]]:
    """
    Taquilla event pages (/entradas/...) often aggregate multiple cities.
    Extract only Zaragoza occurrences, including multiple session times.
    """
    if not detail_html:
        return []
    soup = BeautifulSoup(detail_html, "html.parser")
    out: List[Dict[str, Any]] = []
    for li in soup.select("ul.ent-results-list > li[itemtype*='MusicEvent']"):
        addr_loc = li.select_one("meta[itemprop='addressLocality']")
        if not addr_loc or (addr_loc.get("content") or "").strip().lower() != "zaragoza":
            continue
        venue_m = li.select_one("div[itemprop='location'] meta[itemprop='name']")
        venue = (venue_m.get("content") or "").strip() if venue_m else ""
        start_m = li.select_one("meta[itemprop='startDate']")
        start_raw = (start_m.get("content") or "").strip() if start_m else ""
        d = _parse_date_ddmmyyyy(start_raw) or (
            datetime.strptime(start_raw[:10], "%Y-%m-%d").date() if start_raw else None
        )
        if not d:
            continue

        times = []
        for tspan in li.select(".ent-results-list-hour-time span"):
            txt = tspan.get_text(" ", strip=True)
            m = re.search(r"\b(\d{1,2}):(\d{2})\b", txt)
            if not m:
                continue
            times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        times = list(dict.fromkeys(times))  # stable unique
        if not times:
            times = [None]

        # Price: use aggregate lowPrice when available
        low_m = li.select_one("div[itemprop='offers'] meta[itemprop='lowPrice']")
        price_min_eur: Optional[float] = None
        if low_m and low_m.get("content"):
            try:
                price_min_eur = float(str(low_m["content"]).strip())
            except ValueError:
                price_min_eur = None
        price_text: Optional[str] = None
        price_span = li.select_one(".ent-results-list-hour-price span")
        if price_span:
            pt = price_span.get_text(" ", strip=True)
            pt = re.sub(r"\s+", " ", pt).strip()
            if pt:
                price_text = pt[:120]

        for tt in times:
            out.append(
                {
                    "venue": venue or None,
                    "venue_slug": _slugify_venue(venue) if venue else None,
                    "date_from": d,
                    "date_to": d,
                    "time_text": tt,
                    "price_text": price_text,
                    "price_min_eur": price_min_eur,
                }
            )
    return out


def _extract_events_from_list_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")

    # Cabeceras con el texto "* en Zaragoza"
    category_headings = [
        ("h1", "Todas las entradas en Zaragoza"),
        ("h2", "Espectáculos en Zaragoza"),
        ("h2", "Conciertos en Zaragoza"),
        ("h2", "Deportes en Zaragoza"),
        ("h2", "Museos y Visitas Guiadas en Zaragoza"),
        ("h2", "Recintos destacados en Zaragoza"),
    ]

    found: Dict[str, Dict[str, Any]] = {}
    for tag_name, expected_text in category_headings:
        heading = soup.find(tag_name, string=lambda s: s and s.strip() == expected_text)
        if not heading:
            continue

        # Recorrer los nodos posteriores a la cabecera hasta la siguiente cabecera "en Zaragoza"
        for el in heading.next_elements:
            if getattr(el, "name", None) in {"h1", "h2", "h3"}:
                txt = el.get_text(" ", strip=True)
                if "en Zaragoza" in txt and txt != expected_text:
                    break

            if getattr(el, "name", None) == "div":
                classes = el.get("class") or []
                if "d-mosaic__box" in classes and el.get("data-link"):
                    ev = _card_to_event(el, expected_text)
                    if ev:
                        # Deduplicar por URL de detalle
                        found[ev["detail_url"]] = ev

    # Materializar en lista
    events = list(found.values())
    return events


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        # Si el esquema del cache no coincide (p.ej. añadimos `price_min_eur`),
        # forzamos refresco.
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).date()
        # TTL en segundos -> comparamos con datetime para precisión
        # Para simplificar: si es "hoy", lo tratamos como válido.
        # (El scraper es liviano y cacheado; la precisión no es crítica aquí.)
        if (date.today() - fetched_at).days * 86400 <= ttl_seconds:
            events = payload["events"]
            for e in events:
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
                e.setdefault("price_min_eur", None)
                e.setdefault("time_text", None)
                e.setdefault("venue", None)
                e.setdefault("venue_slug", None)
            return events
    except Exception:
        return None
    return None


def _save_cache(events: List[Dict[str, Any]]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.utcnow().isoformat(),
        "schema_version": _CACHE_SCHEMA_VERSION,
        "events": [
            {
                **{k: v for k, v in e.items() if k not in {"date_from", "date_to"}},
                "date_from": e["date_from"].isoformat(),
                "date_to": e["date_to"].isoformat(),
            }
            for e in events
        ],
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_venue_from_detail_html(html: str) -> Optional[str]:
    """
    Taquilla muestra una frase del tipo:
      "Ver eventos disponibles en Auditorio de Zaragoza">Auditorio de Zaragoza, Zaragoza"
    Dentro del HTML puede haber múltiples localizaciones; aquí elegimos la que contenga Zaragoza.
    """
    if not html:
        return None

    # Captura todo el bloque "X">... hasta el siguiente "<"
    matches = re.findall(r"Ver eventos disponibles en\s*([^<]{3,160})", html, flags=re.IGNORECASE)
    for m in matches:
        if "Zaragoza" not in m:
            continue
        # Suele venir como: 'Auditorio de Zaragoza">Auditorio de Zaragoza, Zaragoza'
        venue = m.split('">')[0]
        venue = venue.split(" - ")[0].strip()
        venue = venue.strip(" ,|")
        if venue:
            return venue
    return None


def _only_zaragoza(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep only events that we could confirm are in Zaragoza.
    The list page sometimes mixes content; the detail-page venue extractor
    only returns a venue when it finds a location containing 'Zaragoza'.
    """
    return [e for e in events if (e.get("venue") or "").strip()]


def _slugify_venue(name: str) -> str:
    name = (name or "").lower().strip()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
        "&": "y",
    }
    for k, v in replacements.items():
        name = name.replace(k, v)
    name = re.sub(r"[^a-z0-9\\-]", "", name)
    name = re.sub(r"\\-+", "-", name).strip("-")
    return name


def _enrich_events_with_venue(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Replace list-derived dates with Zaragoza occurrences from detail pages when available.
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    enriched: List[Dict[str, Any]] = []
    for e in events:
        detail_url = e.get("detail_url")
        if not detail_url:
            e["venue"] = None
            e["venue_slug"] = None
            enriched.append(e)
            continue
        try:
            r = requests.get(detail_url, headers=headers, timeout=30)
            r.raise_for_status()
            occs = _extract_zaragoza_occurrences(r.text)
        except Exception:
            occs = []

        if not occs:
            # Fallback to old venue extractor (may still work on some pages).
            venue = _extract_venue_from_detail_html(getattr(r, "text", "") if "r" in locals() else "")
            e["venue"] = venue
            e["venue_slug"] = _slugify_venue(venue) if venue else None
            enriched.append(e)
            continue

        # Expand: one event per Zaragoza session time (if multiple)
        for occ in occs:
            e2 = dict(e)
            e2["venue"] = occ.get("venue")
            e2["venue_slug"] = occ.get("venue_slug")
            e2["date_from"] = occ["date_from"]
            e2["date_to"] = occ["date_to"]
            e2["time_text"] = occ.get("time_text")
            # Prefer occurrence-level price if present
            if occ.get("price_text"):
                e2["price_text"] = occ.get("price_text")
            if occ.get("price_min_eur") is not None:
                e2["price_min_eur"] = occ.get("price_min_eur")
            enriched.append(e2)

    return enriched


def _fetch_list_page() -> str:
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(LIST_URL, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def scrape_events_list() -> List[Dict[str, Any]]:
    html = _fetch_list_page()
    events = _extract_events_from_list_html(html)
    # Normalizar campos (por si acaso)
    cleaned: List[Dict[str, Any]] = []
    for e in events:
        if not e.get("date_from") or not e.get("date_to"):
            continue
        cleaned.append(e)
    # Orden estable: primero fecha de inicio
    cleaned.sort(key=lambda x: (x["date_from"], x["title"]))
    return cleaned


def get_events() -> List[Dict[str, Any]]:
    """
    Devuelve eventos desde Taquilla para la ciudad configurada.
    Usa caché en disco para no re-scrapear en cada request.
    """
    ttl_seconds = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl_seconds)
    if cached is not None:
        return _only_zaragoza(cached)
    events = scrape_events_list()
    # Enriquecer con salas/recintos desde páginas de detalle
    events = _enrich_events_with_venue(events)
    events = _only_zaragoza(events)
    _save_cache(events)
    return events


# Importación tardía para evitar circular con os en algunas herramientas
import os  # noqa: E402

