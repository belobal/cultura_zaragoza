"""
ZARAGOZALA (agenda "Qué hacer en Zaragoza").
https://zaragozala.com/

The site uses Modern Events Calendar (MEC) and exposes events via WordPress REST:
  /wp-json/wp/v2/mec-events

Each event detail page includes JSON-LD (schema.org/Event) with startDate/endDate and location name.
"""

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://zaragozala.com"
API_LIST = f"{BASE}/wp-json/wp/v2/mec-events"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "zaragozala_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "zaragozala"
CATEGORY = "Zaragozala"
CATEGORY_SLUG = "zaragozala"

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


def _fetch_json(url: str, params: Dict[str, str]) -> Any:
    r = requests.get(url, params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _fetch_html(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
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
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9\\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def _parse_iso_dt_to_date_time_text(s: str) -> Tuple[Optional[date], Optional[str]]:
    """
    JSON-LD startDate examples:
      2026-04-23T23:00:00+02:00
    """
    if not s:
        return None, None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            try:
                d = datetime.strptime(s[:10], "%Y-%m-%d").date()
                return d, None
            except ValueError:
                return None, None
    return dt.date(), f"{dt.hour:02d}:{dt.minute:02d}"


def _parse_price_text_from_jsonld(obj: dict) -> Optional[str]:
    offers = obj.get("offers")
    if isinstance(offers, dict):
        p = offers.get("price")
        if p is not None and str(p).strip():
            return f"Desde {str(p).strip()}€"
    return None


def _parse_event_jsonld(detail_html: str) -> Optional[dict]:
    soup = BeautifulSoup(detail_html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        txt = script.get_text(strip=True)
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event" and data.get("startDate"):
            return data
    return None


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).date()
        if (date.today() - fetched_at).days * 86400 > ttl_seconds:
            return None
        events = payload.get("events", [])
        for e in events:
            e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
            e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
            e.setdefault("time_text", None)
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
        rows = _fetch_json(API_LIST, {"per_page": "50"})
    except Exception:
        rows = []

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    today = date.today()

    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        link = (r.get("link") or "").strip()
        title = ((r.get("title") or {}).get("rendered") if isinstance(r.get("title"), dict) else None) or ""
        title = BeautifulSoup(title, "html.parser").get_text(" ", strip=True)
        if not link or not title:
            continue
        if link in seen:
            continue
        seen.add(link)
        try:
            detail_html = _fetch_html(link)
            obj = _parse_event_jsonld(detail_html) or {}
        except Exception:
            obj = {}

        d_from, time_text = _parse_iso_dt_to_date_time_text(obj.get("startDate") if isinstance(obj, dict) else "")
        d_to, _ = _parse_iso_dt_to_date_time_text(obj.get("endDate") if isinstance(obj, dict) else "")
        if not d_from:
            continue
        if not d_to:
            d_to = d_from
        if d_to < today:
            continue

        loc = obj.get("location") if isinstance(obj, dict) else None
        venue_name: Optional[str] = None
        if isinstance(loc, dict):
            venue_name = loc.get("name") or None
        venue_name = BeautifulSoup(str(venue_name or ""), "html.parser").get_text(" ", strip=True) or None
        venue_slug = _slugify(venue_name) if venue_name else None

        price_text = _parse_price_text_from_jsonld(obj) if isinstance(obj, dict) else None

        out.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": venue_name,
                "venue_slug": venue_slug,
                "date_from": d_from,
                "date_to": d_to,
                "time_text": time_text,
                "price_text": price_text,
                "price_min_eur": None,
                "detail_url": link,
                "source": SOURCE,
            }
        )

    out.sort(key=lambda e: (e["date_from"], e.get("time_text") or "", e["title"]))
    if out:
        _save_cache(out)
    return out

