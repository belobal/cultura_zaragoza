"""
Fiestas del Pilar — programación (Ayuntamiento de Zaragoza).
Portal: https://www.zaragoza.es/sede/portal/cultura/fiestas-pilar/
Datos: dataset-282 (EventON / actos) filtrado por portales.portal.id:11
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

PORTAL_URL = "https://www.zaragoza.es/sede/portal/cultura/fiestas-pilar/"
PROGRAMACION_URL = "https://www.zaragoza.es/sede/portal/cultura/fiestas-pilar/programacion/"
DATASET_SEARCH_URL = "https://www.zaragoza.es/sede/servicio/data/dataset-282/_search"
PORTAL_ID = 11

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "fiestas_pilar_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 2

SOURCE = "fiestas_pilar"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_WEEKDAY_ES = {
    0: "Lunes",
    1: "Martes",
    2: "Miércoles",
    3: "Jueves",
    4: "Viernes",
    5: "Sábado",
    6: "Domingo",
}

_CATEGORY_MAP = {
    "musica": ("Conciertos", "conciertos-en-zaragoza"),
    "teatro y artes escenicas": ("Teatro", "teatro"),
    "ferias y fiestas": ("Espectáculos", "espectaculos-en-zaragoza"),
    "otros": ("Espectáculos", "espectaculos-en-zaragoza"),
}

# Long-running museum shows, kids/games/sports, etc. drown the agenda.
_SKIP_CATEGORIES = {
    "deporte",
    "ocio y juegos",
    "exposiciones",
    "artes plasticas",
    "visitas turisticas",
    "formacion",
    "medio ambiente y naturaleza",
}

# Title/venue keywords for bullfighting, kids, games and sports (often under Ferias y Fiestas).
_EXCLUDE_TITLE_VENUE_RE = re.compile(
    r"\b("
    r"infantil|ninos|ninas|tragachicos|"
    r"juego|juegos|"
    r"deporte|deportivo|deportiva|gimnasia|frontenis|"
    r"taurin[oa]|toros?|vaquillas?|novillad\w*|corrida|rejone\w*|"
    r"embolador\w*|recortes?"
    r")\b",
    re.IGNORECASE,
)

_EXCLUDE_VENUE_RE = re.compile(
    r"plaza de toros|rio y juego|palacio de deportes|pabellon deportivo",
    re.IGNORECASE,
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "application/json",
        "Accept-Language": "es-ES,es;q=0.9",
        "Content-Type": "application/json",
    }


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


def _slugify_venue(name: str) -> Optional[str]:
    x = _norm(name)
    if not x:
        return None
    x = x.replace("&", "y")
    x = re.sub(r"[^a-z0-9]+", "-", x).strip("-")
    return x or None


def _parse_iso_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    s = str(raw).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _clean_title(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = t.replace("“", '"').replace("”", '"').replace("„", '"')
    return t


def _is_excluded_title_or_venue(title: str, venue: Optional[str]) -> bool:
    blob = _norm(f"{title} {venue or ''}")
    if _EXCLUDE_VENUE_RE.search(blob):
        return True
    return bool(_EXCLUDE_TITLE_VENUE_RE.search(blob))


def _map_category(category_list: Any) -> Optional[Tuple[str, str]]:
    if not isinstance(category_list, list) or not category_list:
        return ("Espectáculos", "espectaculos-en-zaragoza")
    titles = []
    for c in category_list:
        if isinstance(c, dict) and c.get("title"):
            titles.append(str(c["title"]))
        elif isinstance(c, str):
            titles.append(c)
    # If any category is skipped and none maps, drop the event.
    mapped = None
    skip_only = True
    for title in titles:
        n = _norm(title)
        if n in _SKIP_CATEGORIES:
            continue
        skip_only = False
        if n in _CATEGORY_MAP:
            return _CATEGORY_MAP[n]
        mapped = ("Espectáculos", "espectaculos-en-zaragoza")
    if skip_only:
        return None
    return mapped


def _detail_url(event_id: Any) -> str:
    if event_id is not None:
        return f"https://www.zaragoza.es/sede/servicio/cultura/evento/{event_id}"
    return PROGRAMACION_URL


def _sessions_for_subevent(se: Dict[str, Any]) -> List[Tuple[date, Optional[str]]]:
    """
    Expand a subEvent into (date, HH:MM) when openingHours exist.
    Returns [] when there are no session times — caller keeps date_from/date_to span.
    """
    d0 = _parse_iso_date(se.get("startDate"))
    d1 = _parse_iso_date(se.get("endDate")) or d0
    if not d0:
        return []
    if d1 < d0:
        d1 = d0

    hours = se.get("openingHours") or []
    by_wd: Dict[str, List[str]] = {}
    all_times: List[str] = []
    for oh in hours:
        if not isinstance(oh, dict):
            continue
        t = (oh.get("startTime") or "").strip()
        if not t:
            continue
        if len(t) >= 5 and t[2] == ":":
            t = t[:5]
        all_times.append(t)
        wd = (oh.get("dayOfWeek") or "").strip()
        if wd:
            by_wd.setdefault(wd, []).append(t)

    if not all_times:
        return []

    out: List[Tuple[date, Optional[str]]] = []
    d = d0
    while d <= d1:
        wd = _WEEKDAY_ES[d.weekday()]
        times = by_wd.get(wd) or []
        if not times and d0 == d1:
            times = list(dict.fromkeys(all_times))
        for t in dict.fromkeys(times):
            out.append((d, t))
        d += timedelta(days=1)
    return out


def _fetch_portal_events() -> List[Dict[str, Any]]:
    payload = {
        "query": {"query_string": {"query": f"portales.portal.id:{PORTAL_ID}"}},
        "size": 1000,
    }
    r = requests.post(DATASET_SEARCH_URL, headers=_headers(), json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    hits = ((data.get("hits") or {}).get("hits")) or []
    out: List[Dict[str, Any]] = []
    for h in hits:
        src = h.get("_source")
        if isinstance(src, dict):
            out.append(src)
    return out


def scrape_events_list() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set = set()
    today = date.today()

    for ev in _fetch_portal_events():
        title = _clean_title(ev.get("title") or "")
        if not title:
            continue
        # Food Trucks have their own scraper; skip them here to avoid duplicates.
        if re.search(r"food\s*trucks?", title, re.IGNORECASE):
            continue
        mapped = _map_category(ev.get("category"))
        if mapped is None:
            continue
        category, category_slug = mapped
        if category_slug == "infantil":
            continue
        detail = _detail_url(ev.get("id"))

        subevents = ev.get("subEvent") or []
        if not subevents:
            d0 = _parse_iso_date(ev.get("startDate"))
            d1 = _parse_iso_date(ev.get("endDate")) or d0
            if not d0:
                continue
            subevents = [
                {
                    "startDate": d0.isoformat(),
                    "endDate": (d1 or d0).isoformat(),
                    "openingHours": [],
                    "location": ev.get("location") if isinstance(ev.get("location"), dict) else {},
                }
            ]

        for se in subevents:
            if not isinstance(se, dict):
                continue
            loc = se.get("location") if isinstance(se.get("location"), dict) else {}
            venue = (loc.get("title") or "").strip() or None
            if _is_excluded_title_or_venue(title, venue):
                continue
            venue_slug = _slugify_venue(venue) if venue else None
            d0 = _parse_iso_date(se.get("startDate"))
            d1 = _parse_iso_date(se.get("endDate")) or d0
            if not d0:
                continue
            if d1 and d1 < d0:
                d1 = d0

            sessions = _sessions_for_subevent(se)
            rows: List[Tuple[date, date, Optional[str]]] = []
            if sessions:
                for d, time_text in sessions:
                    rows.append((d, d, time_text))
            else:
                rows.append((d0, d1 or d0, None))

            for date_from, date_to, time_text in rows:
                if date_to < today:
                    continue
                key = (
                    date_from.isoformat(),
                    date_to.isoformat(),
                    venue_slug or "",
                    title.lower(),
                    time_text or "",
                )
                if key in seen:
                    continue
                seen.add(key)
                events.append(
                    {
                        "title": title,
                        "category": category,
                        "category_slug": category_slug,
                        "venue": venue,
                        "venue_slug": venue_slug,
                        "date_from": date_from,
                        "date_to": date_to,
                        "time_text": time_text,
                        "price_text": None,
                        "price_min_eur": None,
                        "detail_url": detail,
                        "source": SOURCE,
                    }
                )

    events.sort(
        key=lambda e: (
            e["date_from"],
            e.get("time_text") or "",
            (e.get("venue") or "").lower(),
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
        fetched_at = payload.get("fetched_at")
        if fetched_at and ttl_seconds > 0:
            try:
                age = (datetime.utcnow() - datetime.fromisoformat(str(fetched_at))).total_seconds()
                if age > ttl_seconds:
                    return None
            except Exception:
                return None
        events = payload.get("events") or []
        cleaned: List[Dict[str, Any]] = []
        for e in events:
            e2 = dict(e)
            if isinstance(e2.get("date_from"), str):
                e2["date_from"] = datetime.strptime(e2["date_from"], "%Y-%m-%d").date()
            if isinstance(e2.get("date_to"), str):
                e2["date_to"] = datetime.strptime(e2["date_to"], "%Y-%m-%d").date()
            e2.setdefault("source", SOURCE)
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
