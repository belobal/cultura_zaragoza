"""
Scraper de eventos de La Lata de Bombillas (Zaragoza).
https://lalatadebombillas.es/ — listado The Events Calendar.
"""
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup

BASE = "https://lalatadebombillas.es"
LIST_URLS = [
    f"{BASE}/eventos/lista/",
    f"{BASE}/eventos/lista/?eventDisplay=upcoming",
]
# Venta de entradas (catálogo propio / enlaces externos tipo Dice)
ENTRADAS_WEB_URL = (
    "https://entradas.lalatadebombillas.es/web/"
    "?menu=36&pagina=&siteID=latadebombillas"
)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "lalata_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 3

SOURCE_TICKETS = "lalata_entradas"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

SOURCE = "lalata"
VENUE_NAME = "La Lata de Bombillas"
VENUE_SLUG = "la-lata-de-bombillas"

VENUE_SALA_LOPEZ_NAME = "Sala López"
VENUE_SALA_LOPEZ_SLUG = "sala-lopez-zaragoza"

# Meses abreviados (web de entradas: Mar, Abr, May…)
_ENTRADAS_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _slugify_category(name: str) -> str:
    name = name.lower().strip()
    for k, v in {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        " ": "-", "&": "y",
    }.items():
        name = name.replace(k, v)
    name = re.sub(r"[^a-z0-9\-]", "", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name


def _norm_dedupe_title(title: str) -> str:
    """Clave suave para fusionar WordPress + web de entradas."""
    t = (title or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-záéíóúñ0-9 ]", "", t)
    return t.strip()


def _venue_from_entradas_place(place_raw: str) -> tuple[str, str]:
    p = (place_raw or "").lower()
    if "sala lópez" in p or "sala lopez" in p:
        return VENUE_SALA_LOPEZ_NAME, VENUE_SALA_LOPEZ_SLUG
    return VENUE_NAME, VENUE_SLUG


def _category_for_venue(venue_name: str) -> tuple[str, str]:
    cat_name = f"{venue_name} (Conciertos)"
    return cat_name, _slugify_category(cat_name)


def _parse_entradas_ticket_date(article: Any) -> Optional[date]:
    d_el = article.select_one(".mkp-ticket-date-monthday")
    m_el = article.select_one(".mkp-ticket-date-month")
    y_el = article.select_one(".mkp-ticket-date-year")
    if not (d_el and m_el and y_el):
        return None
    try:
        day = int(d_el.get_text(strip=True))
        year = int(y_el.get_text(strip=True))
    except ValueError:
        return None
    mon_raw = m_el.get_text(strip=True).lower()[:3]
    month = _ENTRADAS_MONTHS.get(mon_raw)
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _ticket_status_from_btn(btn: Any) -> tuple[Optional[str], Optional[str]]:
    """(price_text, internal status: cancelado|agotadas|None)."""
    if not btn:
        return None, None
    label = btn.get_text(" ", strip=True).lower()
    if "cancelado" in label:
        return "Cancelado", "cancelado"
    if "agotadas" in label or "agotada" in label:
        return "Agotadas", "agotadas"
    return None, None


def _scrape_entradas_web_events() -> List[Dict[str, Any]]:
    """Eventos publicados en entradas.lalatadebombillas.es (listado taquilla propia)."""
    try:
        html = _fetch(ENTRADAS_WEB_URL)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    for art in soup.select("article.mkp-ticket-item"):
        title_el = art.select_one("h2.mkp-ticket-data-title")
        place_el = art.select_one("p.mkp-ticket-data-place")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        place_raw = place_el.get_text(" ", strip=True) if place_el else ""
        d = _parse_entradas_ticket_date(art)
        if not d or not title:
            continue
        btn = art.select_one("a.mkp-btn[href]")
        href = (btn.get("href") or "").strip() if btn else None
        if not href:
            continue
        venue_name, venue_slug = _venue_from_entradas_place(place_raw)
        price_text, _st = _ticket_status_from_btn(btn)
        cat_name, cat_slug = _category_for_venue(venue_name)
        out.append(
            {
                "title": title,
                "category": cat_name,
                "category_slug": cat_slug,
                "venue": venue_name,
                "venue_slug": venue_slug,
                "date_from": d,
                "date_to": d,
                "price_text": price_text,
                "price_min_eur": None,
                "detail_url": href,
                "source": SOURCE_TICKETS,
            }
        )
    return out


def _dedupe_key(e: Dict[str, Any]) -> tuple[str, str]:
    return (e["date_from"].isoformat(), _norm_dedupe_title(e["title"]))


def _future_events_only(events: List[Dict[str, Any]], ref: Optional[date] = None) -> List[Dict[str, Any]]:
    """Quita fechas pasadas (el listado HTML del calendario a veces solo tiene eventos viejos)."""
    ref = ref or date.today()
    return [e for e in events if e.get("date_to") and e["date_to"] >= ref]


def _merge_wordpress_and_entradas_web(
    wp_events: List[Dict[str, Any]],
    ticket_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by: Dict[tuple[str, str], Dict[str, Any]] = {}
    for e in wp_events:
        by[_dedupe_key(e)] = dict(e)
    for t in ticket_events:
        k = _dedupe_key(t)
        if k not in by:
            by[k] = dict(t)
            continue
        ex = by[k]
        ex["detail_url"] = t["detail_url"]
        psala = (t.get("venue") or "").lower()
        if "sala lópez" in psala or "sala lopez" in psala:
            ex["venue"] = t["venue"]
            ex["venue_slug"] = t["venue_slug"]
            ex["category"] = t["category"]
            ex["category_slug"] = t["category_slug"]
        if t.get("price_text") in ("Cancelado", "Agotadas"):
            ex["price_text"] = t["price_text"]
            if t["price_text"] == "Cancelado":
                ex["price_min_eur"] = None
    merged = list(by.values())
    merged.sort(key=lambda x: (x["date_from"], x["title"]))
    return merged


def _parse_event_date(article: Any) -> Optional[date]:
    t = article.select_one("time[datetime]")
    if t and t.get("datetime"):
        raw = t["datetime"].strip()
        try:
            if "T" in raw:
                part = raw.split("T")[0]
                return datetime.strptime(part, "%Y-%m-%d").date()
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    span = article.select_one(".tribe-event-date-start")
    if span:
        txt = span.get_text(" ", strip=True)
        # "12 julio 2024 21:00"
        months = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        }
        m = re.match(
            r"^(\d{1,2})\s+([a-záéíóúñ]+)\s+(\d{4})",
            txt.lower(),
            re.I,
        )
        if m:
            d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            mo = months.get(mon)
            if mo:
                try:
                    return date(y, mo, d)
                except ValueError:
                    pass
    return None


def _parse_event_time_text(article: Any) -> Optional[str]:
    """
    Extract start time as HH:MM when present in the listing.
    Examples seen:
      - time[datetime]="2026-07-12T21:00:00+02:00"
      - ".tribe-event-date-start" text like "12 julio 2024 21:00"
    """
    t = article.select_one("time[datetime]")
    if t and t.get("datetime"):
        raw = t["datetime"].strip()
        m = re.search(r"T(\d{2}):(\d{2})", raw)
        if m:
            return f"{m.group(1)}:{m.group(2)}"
    span = article.select_one(".tribe-event-date-start")
    if span:
        txt = span.get_text(" ", strip=True)
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", txt)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _event_url_from_article(article: Any) -> Optional[str]:
    title_a = article.select_one(
        "h3 a[href*='/evento/'], .tribe-events-calendar-latest-past__event-title-link[href*='/evento/']"
    )
    if title_a and title_a.get("href"):
        return title_a["href"].split("#")[0]
    for a in article.select("a[href*='/evento/']"):
        h = a["href"].split("#")[0]
        if "/evento/" in h and h.rstrip("/").count("/") >= 4:
            return h
    return None


def _title_from_article(article: Any) -> Optional[str]:
    title_a = article.select_one(
        "h3 a[href*='/evento/'], .tribe-events-calendar-latest-past__event-title-link[href*='/evento/']"
    )
    if title_a:
        return title_a.get_text(" ", strip=True)
    return None


def _extract_price_from_schedule(html: str):
    soup = BeautifulSoup(html, "html.parser")
    sch = soup.select_one(".tribe-events-schedule")
    if not sch:
        return None, None
    text = sch.get_text(" ", strip=True)
    m = re.search(r"(\d+[.,]?\d*)\s*€", text)
    if not m:
        return None, None
    raw = m.group(1).replace(",", ".")
    try:
        price = float(raw)
    except ValueError:
        return None, None
    return price, f"Desde {m.group(1)}€"


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _parse_list_page(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    for art in soup.select("article.type-tribe_events"):
        url = _event_url_from_article(art)
        title = _title_from_article(art)
        d = _parse_event_date(art)
        time_text = _parse_event_time_text(art)
        if not url or not title or not d:
            continue
        out.append(
            {
                "detail_url": url,
                "title": title,
                "date_from": d,
                "date_to": d,
                "time_text": time_text,
            }
        )
    return out


def _merge_by_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_url: Dict[str, Dict[str, Any]] = {}
    for it in items:
        u = it["detail_url"]
        if u not in by_url:
            by_url[u] = it
    return list(by_url.values())


def _enrich_with_prices(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for e in events:
        try:
            html = _fetch(e["detail_url"])
            price_eur, price_text = _extract_price_from_schedule(html)
        except Exception:
            price_eur, price_text = None, None
        cat_name = f"{VENUE_NAME} (Conciertos)"
        enriched.append(
            {
                "title": e["title"],
                "category": cat_name,
                "category_slug": _slugify_category(cat_name),
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": e["date_from"],
                "date_to": e["date_to"],
                "time_text": e.get("time_text"),
                "price_text": price_text,
                "price_min_eur": price_eur,
                "detail_url": e["detail_url"],
                "source": SOURCE,
            }
        )
    enriched.sort(key=lambda x: (x["date_from"], x["title"]))
    return enriched


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).date()
        if (date.today() - fetched_at).days * 86400 <= ttl_seconds:
            events = payload["events"]
            for e in events:
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
                e.setdefault("venue", VENUE_NAME)
                e.setdefault("venue_slug", VENUE_SLUG)
            return events
    except Exception:
        return None
    return None


def _save_cache(events: List[Dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "fetched_at": datetime.utcnow().isoformat(),
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


def _scrape_wordpress_enriched() -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    merged: List[Dict[str, Any]] = []
    for list_url in LIST_URLS:
        try:
            html = _fetch(list_url)
        except Exception:
            continue
        for row in _parse_list_page(html):
            u = row["detail_url"]
            if u in seen:
                continue
            seen.add(u)
            merged.append(row)
    merged = _merge_by_url(merged)
    return _enrich_with_prices(merged)


def scrape_events_list() -> List[Dict[str, Any]]:
    wp = _scrape_wordpress_enriched()
    tickets = _scrape_entradas_web_events()
    merged = _merge_wordpress_and_entradas_web(wp, tickets)
    return _future_events_only(merged)


def get_events() -> List[Dict[str, Any]]:
    ttl = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl)
    if cached is not None:
        fut = _future_events_only(cached)
        if fut:
            return fut
        # Caché con eventos ya pasados (p. ej. solo el HTML viejo del calendario).
        if cached:
            try:
                CACHE_FILE.unlink()
            except Exception:
                pass
    try:
        events = scrape_events_list()
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events
