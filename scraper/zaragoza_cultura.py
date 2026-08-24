"""
Scraper de eventos desde la web Cultura del Ayuntamiento de Zaragoza.
https://www.zaragoza.es/sede/servicio/cultura/

Objetivo:
- Extraer los eventos listados en la sección "Actividades"/destacados (links a /evento/<id>).
- Excluir "Deportes" y actividades infantiles (Infancia/Infantil/Niños/Niñas) usando facetas `fq=` del propio HTML.
"""

import json
import os
import re
import urllib.parse
from datetime import date, datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://www.zaragoza.es"
LIST_URL = "https://www.zaragoza.es/sede/portal/cultura/actividades"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "zaragoza_cultura_events.json"

DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 2

SOURCE = "zaragoza_cultura"
CATEGORY_NAME = "Zaragoza Cultura"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
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
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9\\-]", "", s)
    s = re.sub(r"\\-+", "-", s).strip("-")
    return s or "unknown"


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


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
                e.setdefault("source", SOURCE)
                e.setdefault("category", CATEGORY_NAME)
                e.setdefault("category_slug", _slugify(CATEGORY_NAME))
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


def _extract_event_ids(list_html: str) -> List[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    ids: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/sede/servicio/cultura/+evento/(\d+)", href)
        if m:
            ids.add(m.group(1))
    return sorted(ids)


def _extract_facet_values_from_html(html: str) -> Set[str]:
    """
    Lee los enlaces `?fq=...` en la página del evento y extrae los valores entre comillas.
    Ej: fq=temas_smultiple%3A("Música") -> {"Música"}
    """
    soup = BeautifulSoup(html, "html.parser")
    values: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "sede/servicio/cultura/evento" not in href:
            continue
        if "fq=" not in href:
            continue

        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        fq_vals = qs.get("fq", [])
        if not fq_vals:
            # A veces viene sin querystring bien formado; intentar fallback por regex
            m = re.search(r"fq=([^&]+)", href)
            if not m:
                continue
            fq_vals = [m.group(1)]

        for fq in fq_vals:
            decoded = urllib.parse.unquote(fq)
            # Extraer todo lo que esté entre comillas dobles
            for match in re.findall(r'\"([^\"]+)\"', decoded):
                v = html_unescape(match).strip()
                if v:
                    values.add(v)
            # Fallback por comillas simples (menos habitual)
            for match in re.findall(r"'([^']+)'", decoded):
                v = html_unescape(match).strip()
                if v:
                    values.add(v)

    # Los facetas también suelen venir en `data-href` con el valor visible como texto del enlace.
    for a in soup.find_all("a", attrs={"data-href": True}):
        dh = a.get("data-href") or ""
        if "sede/servicio/cultura/evento" not in dh:
            continue
        if "fq=" not in dh:
            continue

        label = a.get_text(" ", strip=True)
        if label:
            values.add(html_unescape(label).strip())

        for m in re.findall(r"fq=[^\"']*?\\(([^)]+)\\)", dh):
            # intente extraer el trozo dentro de (...) para luego sacar comillas
            chunk = urllib.parse.unquote(m)
            for match in re.findall(r'\"([^\"]+)\"', chunk):
                v = html_unescape(match).strip()
                if v:
                    values.add(v)
            for match in re.findall(r"'([^']+)'", chunk):
                v = html_unescape(match).strip()
                if v:
                    values.add(v)

    # Fallback: a veces `fq=...` aparece en el HTML sin estar dentro de href de un enlace.
    # Capturamos cualquier token "fq=<valor>" y tratamos igual.
    for fq in set(re.findall(r"fq=([^&\"'\\s>]+)", html)):
        decoded = urllib.parse.unquote(fq)
        for match in re.findall(r'\"([^\"]+)\"', decoded):
            v = html_unescape(match).strip()
            if v:
                values.add(v)
        for match in re.findall(r"'([^']+)'", decoded):
            v = html_unescape(match).strip()
            if v:
                values.add(v)

    return values


def _should_exclude_by_facets(facet_values: Set[str]) -> bool:
    lowered = {v.lower() for v in facet_values if v}

    deportes_keywords = {"deportes"}
    infantil_keywords = {"infancia", "infantil", "niños", "niñas"}

    # Corrección para la posible codificación de acentos (por robustez)
    normalized = set()
    for v in lowered:
        normalized.add(v.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u"))

    lowered = normalized
    if any(k in lowered for k in deportes_keywords):
        return True
    if any(k in lowered for k in infantil_keywords):
        return True

    # Algunos eventos usan "Infantil" o "Niños" sin estar en comillas exactas.
    joined = " | ".join(facet_values).lower()
    if "deportes" in joined:
        return True
    if any(x in joined for x in ["infancia", "infantil", "niños", "niñas"]):
        return True
    return False


def _parse_date_from_start_end(start_raw: str, end_raw: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
    """
    startDate puede venir como "2026-03-20T20:00" y endDate como "2026-03-21".
    """
    if not start_raw:
        return None, None
    try:
        start_date = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            start_date = datetime.strptime(start_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None, None

    if not end_raw:
        return start_date, start_date
    try:
        end_date = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            end_date = datetime.strptime(end_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            end_date = start_date
    return start_date, end_date


def _parse_time_text_from_start(start_raw: str) -> Optional[str]:
    """
    Zaragoza Cultura sometimes provides startDate with time, e.g. "2026-03-20T20:00".
    Return HH:MM when present.
    """
    if not start_raw or "T" not in start_raw:
        return None
    m = re.search(r"T(\d{2}):(\d{2})", start_raw)
    if not m:
        return None
    return f"{m.group(1)}:{m.group(2)}"


def _parse_event_detail(detail_html: str, detail_url: str) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(detail_html, "html.parser")

    # JSON-LD (schema.org Event) es lo más estable para fecha/lugar/título
    event_json: Optional[Dict[str, Any]] = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("@type") == "Event" and data.get("name") and data.get("startDate"):
            event_json = data
            break
    if not event_json:
        return None

    title = event_json.get("name")
    start_raw = event_json.get("startDate")
    end_raw = event_json.get("endDate")
    if not title or not start_raw:
        return None

    date_from, date_to = _parse_date_from_start_end(start_raw, end_raw)
    if not date_from or not date_to:
        return None
    time_text = _parse_time_text_from_start(start_raw)

    location_name: Optional[str] = None
    loc = event_json.get("location")
    if isinstance(loc, list) and loc:
        location_name = loc[0].get("name")
    elif isinstance(loc, dict):
        location_name = loc.get("name")

    facet_values = _extract_facet_values_from_html(detail_html)
    if _should_exclude_by_facets(facet_values):
        return None

    # Categoría fija para no contaminar el dropdown con temas (Deportes ya se filtra).
    category = CATEGORY_NAME
    category_slug = _slugify(category)

    venue_slug = _slugify(location_name or "")
    venue_slug = venue_slug if location_name else None

    return {
        "title": str(title).strip(),
        "category": category,
        "category_slug": category_slug,
        "venue": location_name,
        "venue_slug": venue_slug,
        "date_from": date_from,
        "date_to": date_to,
        "time_text": time_text,
        "price_text": None,
        "price_min_eur": None,
        "detail_url": detail_url,
        "source": SOURCE,
    }


def _scrape_events_list() -> List[Dict[str, Any]]:
    list_html = _fetch(LIST_URL)
    ids = _extract_event_ids(list_html)
    events: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for event_id in ids:
        detail_url = f"{BASE}/sede/servicio/cultura//evento/{event_id}"
        if detail_url in seen:
            continue
        try:
            detail_html = _fetch(detail_url)
        except Exception:
            continue
        ev = _parse_event_detail(detail_html, detail_url)
        if not ev:
            continue
        seen.add(detail_url)
        events.append(ev)
    events.sort(key=lambda e: (e["date_from"], e["title"]))
    return events


def get_events() -> List[Dict[str, Any]]:
    ttl = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl)
    if cached is not None:
        return cached
    events = _scrape_events_list()
    if events:
        _save_cache(events)
    return events

