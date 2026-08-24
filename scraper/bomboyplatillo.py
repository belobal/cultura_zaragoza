import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.bomboyplatillo.org/"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "bomboyplatillo_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "bomboyplatillo"
CATEGORY = "Bombo y Platillo"
CATEGORY_SLUG = "bombo-y-platillo"

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


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


_DATE_RE = re.compile(
    r"\b(?:lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo),\s*"
    r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)


def _parse_date_es(s: str) -> Optional[date]:
    if not s:
        return None
    m = _DATE_RE.search(s.strip().lower())
    if not m:
        return None
    day = int(m.group(1))
    mon = _MONTHS_ES.get(m.group(2).strip().lower())
    year = int(m.group(3))
    if not mon:
        return None
    try:
        return date(year, mon, day)
    except ValueError:
        return None


_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")


def _parse_time(s: str) -> Optional[str]:
    m = _TIME_RE.search((s or "").strip())
    return m.group(1) if m else None


def scrape_events_list() -> List[Dict[str, Any]]:
    html = _fetch(BASE_URL)
    soup = BeautifulSoup(html, "html.parser")

    events: List[Dict[str, Any]] = []

    # Each event is rendered as a block like:
    #   div.item-list ...:
    #     Country
    #     Title
    #     Venue
    #     "domingo, 10" "de" "mayo" "de" "2026"
    #     "19:00 h."
    #     "Ver evento" (link)
    for item in soup.select("div.item-list"):
        lines = [t.strip() for t in item.stripped_strings if t and t.strip()]
        if len(lines) < 6:
            continue
        # Find link
        a = item.find("a", href=True, string=lambda t: t and t.strip().lower() == "ver evento")
        detail_url = (a["href"].strip() if a and a.get("href") else "")
        if not detail_url:
            continue

        title = (item.find("h2").get_text(" ", strip=True) if item.find("h2") else "").strip()
        if not title:
            continue
        if "abono" in title.lower():
            continue

        # Venue: best guess is the line after title (ignoring country lines).
        try:
            title_idx = lines.index(title)
        except ValueError:
            title_idx = 1
        venue = lines[title_idx + 1] if title_idx + 1 < len(lines) else ""

        # Date pieces: locate token containing weekday + day.
        d_from: Optional[date] = None
        for i, tok in enumerate(lines):
            if "," in tok and any(ch.isdigit() for ch in tok):
                # Expect: "<weekday>, <day>" then "de" "<month>" "de" "<year>"
                m = re.search(r"(\d{1,2})", tok)
                if not m:
                    continue
                day = m.group(1)
                if i + 4 < len(lines):
                    month = lines[i + 2]
                    year = lines[i + 4]
                    d_from = _parse_date_es(f"domingo, {day} de {month} de {year}")
                break
        if not d_from:
            continue

        # Time: first HH:MM token in the block.
        time_text = None
        for tok in lines:
            time_text = _parse_time(tok)
            if time_text:
                break

        events.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": venue or None,
                "venue_slug": None,
                "date_from": d_from,
                "date_to": d_from,
                "time_text": time_text,
                "price_text": None,
                "price_min_eur": None,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )

    # Stable order
    events.sort(key=lambda x: (x["date_from"], (x.get("venue") or "").lower(), x["title"].lower()))
    return events


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if (datetime.utcnow() - fetched_at).total_seconds() <= ttl_seconds:
            events = payload.get("events") or []
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
    ttl_seconds = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl_seconds)
    if cached is not None:
        return cached
    try:
        events = scrape_events_list()
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events

