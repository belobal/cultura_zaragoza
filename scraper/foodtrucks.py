"""
Las Food Trucks — Fiestas del Pilar (Parque San Pablo).

Source: Ayuntamiento de Zaragoza program JSON
https://www.zaragoza.es/sede/servicio/cultura/evento/programa/1972
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

PROGRAM_ID = 1972
PROGRAM_URL = (
    f"https://www.zaragoza.es/sede/servicio/cultura/evento/programa/{PROGRAM_ID}"
)
PROGRAM_JSON_URL = f"{PROGRAM_URL}.json"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "foodtrucks_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 3

SOURCE = "foodtrucks"
VENUE_NAME = "Parque San Pablo"
VENUE_SLUG = "parque-san-pablo"
CATEGORY = "Conciertos"
CATEGORY_SLUG = "conciertos-en-zaragoza"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_WEEKDAY_ES = {
    0: "lunes",
    1: "martes",
    2: "miercoles",
    3: "jueves",
    4: "viernes",
    5: "sabado",
    6: "domingo",
}


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9",
    }


def _norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u"),
        ("ñ", "n"),
    ):
        s = s.replace(a, b)
    return s


def _is_foodtrucks_event(ev: Dict[str, Any]) -> bool:
    """Keep Food Trucks acts at Parque San Pablo (title/desc/url or venue)."""
    blob = _norm(
        " ".join(
            [
                str(ev.get("title") or ""),
                str(ev.get("description") or ""),
                str(ev.get("url") or ""),
            ]
        )
    )
    blob_compact = re.sub(r"\s+", "", blob)
    foodish = (
        "foodtruck" in blob_compact
        or "foodtrucks" in blob_compact
        or "lasfoodtrucks" in blob_compact
    )

    def _loc_san_pablo(loc: Any) -> bool:
        if not isinstance(loc, dict):
            return False
        title = _norm(loc.get("title") or loc.get("name") or "")
        return "san pablo" in title

    at_san_pablo = False
    for se in ev.get("subEvent") or []:
        if _loc_san_pablo(se.get("location")):
            at_san_pablo = True
            break
    loc = ev.get("location")
    if isinstance(loc, dict) and _loc_san_pablo(loc):
        at_san_pablo = True
    elif isinstance(loc, list):
        at_san_pablo = any(_loc_san_pablo(x) for x in loc)

    if at_san_pablo and foodish:
        return True
    # All San Pablo entries in this program are Food Trucks programming.
    if at_san_pablo:
        return True
    return foodish


PARENT_LABEL = "Las Food Trucks"


def _clean_title(raw: str) -> str:
    original = re.sub(r"\s+", " ", (raw or "").strip())
    t = original
    t = re.sub(
        r"(?i)\bLAS?\s*FOOD\s*TRUCKS?(?:\s*ZARAGOZA)?\.?\s*",
        " ",
        t,
    )
    t = re.sub(r"(?i)\bFOOD\s*TRUCKS?(?:\s*ZARAGOZA)?\.?\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    artist = t or original
    # Avoid duplicating the parent label if the source title is only that.
    if _norm(artist) in {"las food trucks", "food trucks", "food truck"}:
        return PARENT_LABEL
    return f"{PARENT_LABEL} · {artist}"


def _parse_iso_dt(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except ValueError:
        return None


def _fmt_time(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", str(raw).strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), m.group(2)
    # Ayuntamiento sometimes encodes midnight as 23:59
    if hh == 23 and mm == "59":
        hh, mm = 0, "00"
    return f"{hh:02d}:{mm}"


def _weekday_key(label: str) -> str:
    return _norm(label).replace("é", "e").replace("á", "a")


def _daterange(d0: date, d1: date):
    cur = d0
    while cur <= d1:
        yield cur
        cur += timedelta(days=1)


def _sessions_for_subevent(se: Dict[str, Any]) -> List[Tuple[date, Optional[str]]]:
    """Expand a subEvent into (day, HH:MM|None) rows."""
    d0 = _parse_iso_dt(se.get("startDate") or "")
    d1 = _parse_iso_dt(se.get("endDate") or "") or d0
    if not d0:
        return []
    if d1 < d0:
        d0, d1 = d1, d0

    hours = se.get("openingHours") or []
    if not hours:
        return [(d, None) for d in _daterange(d0, d1)]

    by_wd: Dict[str, List[str]] = {}
    all_times: List[str] = []
    for h in hours:
        if not isinstance(h, dict):
            continue
        t = _fmt_time(h.get("startTime"))
        if not t:
            continue
        all_times.append(t)
        wd = _weekday_key(h.get("dayOfWeek") or "")
        if wd:
            by_wd.setdefault(wd, []).append(t)

    out: List[Tuple[date, Optional[str]]] = []
    for d in _daterange(d0, d1):
        wd = _WEEKDAY_ES[d.weekday()]
        times = by_wd.get(wd) or []
        if not times and d0 == d1:
            # Single-day listing: trust the session times even if weekday label drifts.
            times = list(dict.fromkeys(all_times))
        if times:
            for t in dict.fromkeys(times):
                out.append((d, t))
        elif d0 == d1:
            out.append((d, None))
    return out


def _detail_url(ev: Dict[str, Any]) -> str:
    """Public HTML page (not the JSON/API sameAs, which fails in the browser)."""
    eid = ev.get("id")
    if eid:
        return f"https://www.zaragoza.es/sede/servicio/cultura/evento/{eid}"
    return PROGRAM_URL


def _fetch_program_events() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    start = 0
    rows = 100
    total = None
    while True:
        r = requests.get(
            PROGRAM_JSON_URL,
            params={"start": start, "rows": rows},
            headers=_headers(),
            timeout=12,
        )
        r.raise_for_status()
        payload = r.json()
        block = payload.get("events") or {}
        if total is None:
            total = int(block.get("totalCount") or 0)
        batch = block.get("result") or []
        if not batch:
            break
        out.extend(batch)
        start += len(batch)
        if total and start >= total:
            break
        if len(batch) < rows:
            break
    return out


def scrape_events_list() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set = set()

    for ev in _fetch_program_events():
        if not isinstance(ev, dict) or not _is_foodtrucks_event(ev):
            continue
        title = _clean_title(ev.get("title") or "")
        if not title:
            continue
        detail = _detail_url(ev)
        subevents = ev.get("subEvent") or []
        if not subevents:
            # Fallback to top-level dates
            d0 = _parse_iso_dt(ev.get("startDate") or "")
            d1 = _parse_iso_dt(ev.get("endDate") or "") or d0
            if not d0:
                continue
            subevents = [{"startDate": d0.isoformat(), "endDate": d1.isoformat(), "openingHours": []}]

        for se in subevents:
            if not isinstance(se, dict):
                continue
            loc = se.get("location") or {}
            if isinstance(loc, dict) and loc.get("title"):
                # Prefer San Pablo rows when an event lists several places
                if "san pablo" not in _norm(loc.get("title") or ""):
                    # Still allow if title marked food trucks but venue missing/other
                    if (ev.get("subEvent") and len(ev["subEvent"]) > 1):
                        continue
            for d, time_text in _sessions_for_subevent(se):
                key = (d.isoformat(), time_text or "", title.lower())
                if key in seen:
                    continue
                seen.add(key)
                events.append(
                    {
                        "title": title,
                        "category": CATEGORY,
                        "category_slug": CATEGORY_SLUG,
                        "venue": VENUE_NAME,
                        "venue_slug": VENUE_SLUG,
                        "date_from": d,
                        "date_to": d,
                        "time_text": time_text or "Por determinar",
                        "price_text": "Gratuito",
                        "price_min_eur": 0.0,
                        "detail_url": detail,
                        "source": SOURCE,
                    }
                )

    events.sort(
        key=lambda e: (
            e["date_from"],
            e.get("time_text") or "",
            e["title"].lower(),
        )
    )
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
