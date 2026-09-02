import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "aragonenvivo_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 2

SOURCE = "aragonenvivo"
CATEGORY = "Aragón en Vivo"
CATEGORY_SLUG = "aragon-en-vivo"

# User-facing listing URL; list view under /eventos/lista/ has the same cards.
BASE_URL = "https://aragonenvivo.com/eventos/lista/"
LISTING_URL = "https://aragonenvivo.com/eventos/"


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


def _fetch(url: str, params: Optional[Dict[str, str]] = None) -> str:
    r = requests.get(url, params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _parse_day_month_year_es(day: str, month_name: str, year: str) -> Optional[date]:
    try:
        d = int(str(day).strip())
        y = int(str(year).strip())
    except ValueError:
        return None
    m = _MONTHS_ES.get((month_name or "").strip().lower())
    if not m:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")


def _parse_time_text(s: str) -> Optional[str]:
    m = _TIME_RE.search((s or "").strip())
    return m.group(1) if m else None


def _slugify_venue(name: str) -> Optional[str]:
    x = (name or "").lower().strip()
    if not x:
        return None
    repl = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
        "&": "y",
    }
    for k, v in repl.items():
        x = x.replace(k, v)
    x = re.sub(r"[^a-z0-9\-]", "", x)
    x = re.sub(r"-+", "-", x).strip("-")
    return x or None


def _only_zaragoza_event(venue_line: str, meta_line: str) -> bool:
    blob = f"{venue_line} {meta_line}".lower()
    # Typical lines contain "... Zaragoza, Zaragoza, España"
    return "zaragoza" in blob


def _extract_events_from_list_page(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    events: List[Dict[str, Any]] = []

    # The Events Calendar list view entries.
    for item in soup.select(".tribe-events-calendar-list__event"):
        title_a = item.select_one(".tribe-events-calendar-list__event-title a")
        title = (title_a.get_text(" ", strip=True) if title_a else "").strip()
        detail_url = (title_a.get("href") if title_a else "") or ""

        if not title or not detail_url:
            continue

        # Date is usually split, but as fallback we can parse from the datetime attribute.
        dt_el = item.select_one("time[datetime]")
        d_from: Optional[date] = None
        if dt_el and dt_el.get("datetime"):
            try:
                d_from = datetime.fromisoformat(dt_el["datetime"][:19]).date()
            except Exception:
                d_from = None

        if d_from is None:
            # Fallback: parse "6 de mayo" + current/visible year.
            when = item.get_text(" ", strip=True)
            m = re.search(r"\b(\d{1,2})\s+de\s+([a-záéíóúñ]+)\b", when, re.IGNORECASE)
            y = re.search(r"\b(20\d{2})\b", when)
            if m and y:
                d_from = _parse_day_month_year_es(m.group(1), m.group(2), y.group(1))
        if d_from is None:
            continue

        time_text = None
        time_el = item.select_one(".tribe-event-date-start, .tribe-events-calendar-list__event-datetime")
        if time_el:
            time_text = _parse_time_text(time_el.get_text(" ", strip=True))
        if time_text is None:
            time_text = _parse_time_text(item.get_text(" ", strip=True))

        venue_el = item.select_one(".tribe-events-calendar-list__event-venue-title")
        venue = (venue_el.get_text(" ", strip=True) if venue_el else "").strip()

        addr_el = item.select_one(".tribe-events-calendar-list__event-venue-address")
        addr = (addr_el.get_text(" ", strip=True) if addr_el else "").strip()

        if not _only_zaragoza_event(addr, venue):
            continue

        price_el = item.select_one(".tribe-events-calendar-list__event-cost")
        price_text = (price_el.get_text(" ", strip=True) if price_el else "").strip() or None

        events.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": venue or None,
                "venue_slug": _slugify_venue(venue) if venue else None,
                "date_from": d_from,
                "date_to": d_from,
                "time_text": time_text,
                "price_text": price_text,
                "price_min_eur": None,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )

    return events


def _next_page_url(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one(".tribe-events-c-nav__list-item--next a[href]")
    if a and a.get("href"):
        return str(a["href"]).strip()
    return None


def scrape_events_list() -> List[Dict[str, Any]]:
    # Walk list pagination a few pages, then horizon-filter.
    max_pages = int(os.environ.get("ARAGONENVIVO_MAX_PAGES", "8"))
    html = _fetch(BASE_URL)
    all_events: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    page = 0
    while html and page < max_pages:
        for e in _extract_events_from_list_page(html):
            u = e.get("detail_url") or ""
            if u in seen_urls:
                continue
            seen_urls.add(u)
            all_events.append(e)
        nxt = _next_page_url(html)
        if not nxt:
            break
        try:
            html = _fetch(nxt)
        except Exception:
            break
        page += 1

    # Filter horizon: today -> +90 days (similar to others, configurable).
    horizon_days = int(os.environ.get("ARAGONENVIVO_HORIZON_DAYS", "90"))
    today = date.today()
    end = today + timedelta(days=horizon_days)
    out = [e for e in all_events if e["date_to"] >= today and e["date_from"] <= end]
    out.sort(key=lambda x: (x["date_from"], (x.get("venue") or "").lower(), x["title"].lower()))
    return out


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = payload.get("fetched_at")
        if fetched_at and ttl_seconds > 0:
            try:
                age = (datetime.utcnow() - datetime.fromisoformat(fetched_at)).total_seconds()
                if age > ttl_seconds:
                    return None
            except Exception:
                pass
        events = payload.get("events") or []
        cleaned: List[Dict[str, Any]] = []
        for e in events:
            e2 = dict(e)
            if isinstance(e2.get("date_from"), str):
                e2["date_from"] = datetime.strptime(e2["date_from"], "%Y-%m-%d").date()
            if isinstance(e2.get("date_to"), str):
                e2["date_to"] = datetime.strptime(e2["date_to"], "%Y-%m-%d").date()
            if e2.get("venue") and not e2.get("venue_slug"):
                e2["venue_slug"] = _slugify_venue(e2["venue"])
            cleaned.append(e2)
        return cleaned
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

