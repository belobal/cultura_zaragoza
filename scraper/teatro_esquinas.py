"""
Teatro de las Esquinas (Zaragoza) — programación.
https://www.teatrodelasesquinas.com/es/programacion-de-teatro-y-conciertos.html
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://www.teatrodelasesquinas.com/es/programacion-de-teatro-y-conciertos.html"
BASE = "https://www.teatrodelasesquinas.com"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "teatro_esquinas_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "teatro_esquinas"
VENUE_NAME = "Teatro de las Esquinas"
VENUE_SLUG = "teatro-de-las-esquinas"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# VI 18.09.26 | 21:00 h  OR  Del MA 22.09.26 al MA 20.10.26 | 20:00 h
_DATE_RE = re.compile(
    r"(?:Del\s+)?"
    r"(?:LU|MA|MI|JU|VI|SA|DO)\s+(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})\.(?P<y1>\d{2})"
    r"(?:\s+al\s+(?:LU|MA|MI|JU|VI|SA|DO)\s+(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\.(?P<y2>\d{2}))?"
    r"(?:\s*\|\s*(?P<t>\d{1,2}:\d{2})\s*h)?",
    re.IGNORECASE,
)

_GENRE_MAP = [
    (re.compile(r"\bHUMOR\b", re.I), ("Comedia", "comedia")),
    (re.compile(r"\bTEATRO\b", re.I), ("Teatro", "teatro")),
    (re.compile(r"\bM[UÚ]SICA\b", re.I), ("Conciertos", "conciertos-en-zaragoza")),
    (re.compile(r"\bDANZA\b", re.I), ("Danza", "danza")),
    (re.compile(r"\bFAMILIAR\b", re.I), ("Infantil", "infantil")),
    (re.compile(r"\bMAGIA\b", re.I), ("Espectáculos", "espectaculos-en-zaragoza")),
]


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.text


def _year(yy: str) -> int:
    y = int(yy)
    return 2000 + y if y < 100 else y


def _parse_schedule(text: str) -> Tuple[Optional[date], Optional[date], Optional[str]]:
    m = _DATE_RE.search(text or "")
    if not m:
        return None, None, None
    try:
        d0 = date(_year(m.group("y1")), int(m.group("m1")), int(m.group("d1")))
    except ValueError:
        return None, None, None
    d1 = d0
    if m.group("d2"):
        try:
            d1 = date(_year(m.group("y2")), int(m.group("m2")), int(m.group("d2")))
        except ValueError:
            d1 = d0
    if d1 < d0:
        d0, d1 = d1, d0
    t = m.group("t")
    time_text = None
    if t:
        hh, mm = t.split(":")
        time_text = f"{int(hh):02d}:{mm}"
    return d0, d1, time_text


def _category_from_blob(blob: str) -> Tuple[str, str]:
    for rx, pair in _GENRE_MAP:
        if rx.search(blob or ""):
            return pair
    return "Espectáculos", "espectaculos-en-zaragoza"


def scrape_events_list() -> List[Dict[str, Any]]:
    html = _fetch(URL)
    soup = BeautifulSoup(html, "html.parser")
    today = date.today()
    out: List[Dict[str, Any]] = []
    seen: set = set()

    for box in soup.select(".box_product"):
        title_el = box.select_one(".titol a[href], .titol, h4 a[href], h4")
        title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
        if not title:
            continue
        a = box.select_one('a[href*="/programacion/c/"]')
        href = (a.get("href") if a else "") or ""
        if href and not href.startswith("http"):
            href = urljoin(BASE, href)
        detail_url = href.split("?")[0] if href else URL

        data_el = box.select_one(".data, .data-inici")
        schedule = (data_el.get_text(" ", strip=True) if data_el else "") or ""
        blob = " ".join(box.get_text(" ", strip=True).split())
        d0, d1, time_text = _parse_schedule(schedule or blob)
        if not d0:
            continue
        if d1 < today:
            continue

        # Genre sits after the venue name; avoid matching "Teatro" in "Teatro Las Esquinas".
        genre_blob = blob
        if "esquinas" in genre_blob.lower():
            genre_blob = re.split(r"esquinas", genre_blob, maxsplit=1, flags=re.I)[-1]
        category, category_slug = _category_from_blob(genre_blob)
        key = (title.lower(), d0.isoformat(), d1.isoformat(), time_text or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": title,
                "category": category,
                "category_slug": category_slug,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": d0,
                "date_to": d1,
                "time_text": time_text,
                "price_text": None,
                "price_min_eur": None,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )

    out.sort(key=lambda e: (e["date_from"], e["title"].lower()))
    return out


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
        today = date.today()
        out: List[Dict[str, Any]] = []
        for e in events:
            if isinstance(e.get("date_from"), str):
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
            if isinstance(e.get("date_to"), str):
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
            if e.get("date_to") and e["date_to"] >= today:
                out.append(e)
        return out
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
