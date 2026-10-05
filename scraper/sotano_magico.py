"""
El Sótano Mágico (Zaragoza) — programación / reservas.
https://elsotanomagico.com/reservas/

Uses EventON list markup with schema.org Event meta (startDate, url, title).
Venue: Calle San Pablo 43, Zaragoza.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

URL = "https://elsotanomagico.com/reservas/"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "sotano_magico_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "sotano_magico"
VENUE_NAME = "El Sótano Mágico"
VENUE_SLUG = "el-sotano-magico"

CATEGORY = "Espectáculos"
CATEGORY_SLUG = "espectaculos-en-zaragoza"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_PRICE_RE = re.compile(
    r"(?:precio(?:\s+en\s+taquilla)?|general)\s*[:–-]?\s*(\d+(?:[.,]\d{1,2})?)\s*€?",
    re.IGNORECASE,
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=8)
    r.raise_for_status()
    return r.text


def _parse_iso_datetime(raw: str) -> tuple[Optional[date], Optional[str]]:
    """
    EventON emits values like '2026-10-9T20:00+2:00' (non-zero-padded day/offset).
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    m = re.match(
        r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s](\d{1,2}):(\d{2}))?",
        s,
    )
    if not m:
        return None, None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, None
    time_text = None
    if m.group(4) is not None:
        time_text = f"{int(m.group(4)):02d}:{m.group(5)}"
    return d, time_text


def _extract_price(text: str) -> tuple[Optional[str], Optional[float]]:
    blob = (text or "").strip()
    if not blob:
        return None, None
    m = _PRICE_RE.search(blob)
    if not m:
        m = re.search(r"(\d+(?:[.,]\d{1,2})?)\s*€", blob)
    if not m:
        return None, None
    raw = m.group(1).replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return None, None
    if val.is_integer():
        price_text = f"{int(val)} €"
    else:
        price_text = f"{val:.2f} €".replace(".", ",")
    return price_text, val


def scrape_events_list() -> List[Dict[str, Any]]:
    html = _fetch(URL)
    soup = BeautifulSoup(html, "html.parser")
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in soup.select(".eventon_list_event.event"):
        title_el = item.select_one(".evcal_event_title")
        title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
        if not title:
            continue

        start_el = item.select_one('meta[itemprop="startDate"]')
        end_el = item.select_one('meta[itemprop="endDate"]')
        start_raw = (start_el.get("content") if start_el else "") or ""
        end_raw = (end_el.get("content") if end_el else "") or ""
        date_from, time_text = _parse_iso_datetime(start_raw)
        if date_from is None:
            continue
        date_to, _ = _parse_iso_datetime(end_raw)
        if date_to is None:
            date_to = date_from

        url_el = item.select_one('a[itemprop="url"]')
        detail_url = (url_el.get("href") if url_el else "") or ""
        if not detail_url:
            trig = item.select_one("a.desc_trig[href]")
            detail_url = (trig.get("href") if trig else "") or URL
        detail_url = detail_url.strip()

        dedupe_key = f"{detail_url}|{date_from.isoformat()}|{time_text or ''}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        body_text = item.get_text(" ", strip=True)
        price_text, price_min = _extract_price(body_text)

        events.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": date_from,
                "date_to": date_to,
                "time_text": time_text,
                "price_text": price_text,
                "price_min_eur": price_min,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )

    today = date.today()
    events = [e for e in events if e["date_to"] >= today]
    events.sort(key=lambda e: (e["date_from"], e.get("time_text") or "", e["title"].lower()))
    return events


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
                age = (datetime.utcnow() - datetime.fromisoformat(str(fetched_at))).total_seconds()
                if age > ttl_seconds:
                    return None
            except Exception:
                return None
        cleaned: List[Dict[str, Any]] = []
        for e in payload.get("events") or []:
            e2 = dict(e)
            if isinstance(e2.get("date_from"), str):
                e2["date_from"] = datetime.strptime(e2["date_from"], "%Y-%m-%d").date()
            if isinstance(e2.get("date_to"), str):
                e2["date_to"] = datetime.strptime(e2["date_to"], "%Y-%m-%d").date()
            e2.setdefault("source", SOURCE)
            e2.setdefault("venue", VENUE_NAME)
            e2.setdefault("venue_slug", VENUE_SLUG)
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
    ttl = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    from scraper.cache_policy import STALE_TTL_SECONDS, get_disk_events

    cached = get_disk_events(_load_cache, ttl)
    if cached is not None:
        return cached
    try:
        events = scrape_events_list()
    except Exception:
        events = []
    if events:
        _save_cache(events)
        return events
    return _load_cache(STALE_TTL_SECONDS) or []
