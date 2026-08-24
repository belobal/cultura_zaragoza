import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

URL = "https://creedencesound.com/sala-creedence-conciertos-y-sesiones"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "creedence_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "creedence"
VENUE_NAME = "Sala Creedence"
VENUE_SLUG = "sala-creedence-zaragoza"
CATEGORY = "Conciertos en Zaragoza"
CATEGORY_SLUG = "conciertos-en-zaragoza"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_MONTHS_EN = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

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


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _parse_date_en(text: str) -> Optional[date]:
    # En esta web aparece como "mayo 22, 2026" (también soportamos inglés).
    t = (text or "").strip().lower()
    m = re.search(r"([a-z]+)\s+(\d{1,2}),\s*(\d{4})", t)
    if not m:
        return None
    mon = _MONTHS_EN.get(m.group(1)) or _MONTHS_ES.get(m.group(1))
    if not mon:
        return None
    try:
        return date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None


def _parse_events(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    seen_urls = set()

    for item in soup.select("div.grid-item"):
        date_el = item.select_one(".post-meta-info")
        title_a = item.select_one("h3 a[href]")
        if not date_el or not title_a:
            continue

        d = _parse_date_en(date_el.get_text(" ", strip=True))
        if not d:
            continue
        title = title_a.get_text(" ", strip=True)
        href = title_a.get("href") or ""
        if not title or not href:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        out.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": d,
                "date_to": d,
                "price_text": None,
                "price_min_eur": None,
                "detail_url": href,
                "source": SOURCE,
            }
        )

    # Solo agenda futura
    today = date.today()
    out = [e for e in out if e["date_to"] >= today]
    out.sort(key=lambda e: (e["date_from"], e["title"]))
    return out


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).date()
        if (date.today() - fetched_at).days * 86400 <= ttl_seconds:
            events = payload.get("events", [])
            for e in events:
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
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


def get_events() -> List[Dict[str, Any]]:
    ttl = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl)
    if cached is not None:
        return cached
    try:
        html = _fetch(URL)
        events = _parse_events(html)
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events

