"""
Fiestas del Pilar — programación musical (Ayuntamiento de Zaragoza).
https://www.zaragoza.es/sede/portal/cultura/servicio/noticia/362207
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

URL = "https://www.zaragoza.es/sede/portal/cultura/servicio/noticia/362207"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "fiestas_pilar_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "fiestas_pilar"
CATEGORY = "Conciertos"
CATEGORY_SLUG = "conciertos-en-zaragoza"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Map section headings → (venue display name, venue_slug)
_VENUE_BY_SECTION = {
    "escenario ambar fuente de goya": (
        "Escenario Ámbar Fuente de Goya",
        "escenario-ambar-fuente-de-goya",
    ),
    "escenario ámbar fuente de goya": (
        "Escenario Ámbar Fuente de Goya",
        "escenario-ambar-fuente-de-goya",
    ),
    "jardin de invierno": ("Jardín de Invierno", "jardin-de-invierno"),
    "jardín de invierno": ("Jardín de Invierno", "jardin-de-invierno"),
    "estacion del norte": ("Estación del Norte", "estacion-del-norte"),
    "estación del norte": ("Estación del Norte", "estacion-del-norte"),
    "escenario de raiz": ("Plaza Salamero", "plaza-salamero"),
    "escenario de raíz": ("Plaza Salamero", "plaza-salamero"),
    "plaza salamero": ("Plaza Salamero", "plaza-salamero"),
}

_LINE_RE = re.compile(
    r"^\s*(?P<day>\d{1,2})\s+de\s+(?P<month>[a-záéíóúñ]+)\s*:?\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=12)
    r.raise_for_status()
    return r.text


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    for a, b in (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u"),
        ("ñ", "n"),
    ):
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s)
    return s


def _infer_year(html: str, soup: BeautifulSoup) -> int:
    # Prefer year from the news title / body ("Pilar 2026").
    blob = " "
    if soup.title:
        blob += soup.title.get_text(" ", strip=True) + " "
    h1 = soup.find("h1")
    if h1:
        blob += h1.get_text(" ", strip=True) + " "
    m = re.search(r"\b(20\d{2})\b", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(20\d{2})\b", html[:4000])
    if m:
        return int(m.group(1))
    return date.today().year


def _venue_for_heading(heading: str) -> Optional[Tuple[str, str]]:
    n = _norm(heading)
    if n in _VENUE_BY_SECTION:
        return _VENUE_BY_SECTION[n]
    for key, val in _VENUE_BY_SECTION.items():
        if key in n:
            return val
    return None


def _clean_title(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .")
    # Normalize fancy quotes
    t = t.replace("“", '"').replace("”", '"').replace("„", '"')
    return t


def _parse_line(text: str, year: int) -> Optional[Tuple[date, str]]:
    m = _LINE_RE.match((text or "").replace("\xa0", " ").strip())
    if not m:
        return None
    mon = _MONTHS_ES.get(_norm(m.group("month")))
    if not mon:
        return None
    try:
        d = date(year, mon, int(m.group("day")))
    except ValueError:
        return None
    title = _clean_title(m.group("title"))
    if not title:
        return None
    return d, title


def scrape_events_list() -> List[Dict[str, Any]]:
    html = _fetch(URL)
    soup = BeautifulSoup(html, "html.parser")
    year = _infer_year(html, soup)

    events: List[Dict[str, Any]] = []
    seen: set = set()

    for h3 in soup.find_all("h3"):
        heading = h3.get_text(" ", strip=True)
        venue = _venue_for_heading(heading)
        if not venue:
            continue
        venue_name, venue_slug = venue

        ul = None
        for sib in h3.next_siblings:
            name = getattr(sib, "name", None)
            if name == "h3":
                break
            if name == "ul":
                ul = sib
                break
        if ul is None:
            ul = h3.find_next("ul")
            # Guard: next ul must still belong to this section
            next_h3 = h3.find_next("h3")
            if ul and next_h3 and ul.find_previous("h3") is not h3:
                continue

        if not ul:
            continue

        for li in ul.find_all("li"):
            parsed = _parse_line(li.get_text(" ", strip=True), year)
            if not parsed:
                continue
            d, title = parsed
            key = (d.isoformat(), venue_slug, title.lower())
            if key in seen:
                continue
            seen.add(key)
            events.append(
                {
                    "title": title,
                    "category": CATEGORY,
                    "category_slug": CATEGORY_SLUG,
                    "venue": venue_name,
                    "venue_slug": venue_slug,
                    "date_from": d,
                    "date_to": d,
                    "time_text": "Por determinar",
                    "price_text": None,
                    "price_min_eur": None,
                    "detail_url": URL,
                    "source": SOURCE,
                }
            )

    events.sort(key=lambda e: (e["date_from"], e["venue"] or "", e["title"].lower()))
    return events


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if (datetime.utcnow() - fetched_at).total_seconds() > ttl_seconds:
            return None
        events = payload.get("events") or []
        for e in events:
            if isinstance(e.get("date_from"), str):
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
            if isinstance(e.get("date_to"), str):
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
        return events
    except Exception:
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


def get_events() -> List[Dict[str, Any]]:
    ttl = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl)
    if cached is not None:
        return cached
    try:
        events = scrape_events_list()
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events
