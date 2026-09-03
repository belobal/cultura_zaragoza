"""
Agenda de conciertos desde Conciertos.Club (Zaragoza).
https://conciertos.club/zaragoza

Útil como fuente transversal (puede incluir salas que no cubren otros scrapers).
"""
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://conciertos.club"
LIST_URL = f"{BASE}/zaragoza"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "conciertos_club_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 2

SOURCE = "conciertos_club"
CATEGORY = "Conciertos en Zaragoza"
CATEGORY_SLUG = "conciertos-en-zaragoza"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
    repl = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        " ": "-", "&": "y",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def _abs_url(href: str) -> str:
    h = (href or "").strip()
    if not h:
        return LIST_URL
    if h.startswith("http"):
        return h
    if h.startswith("/"):
        return BASE + h
    return f"{BASE}/{h.lstrip('/')}"


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _spanish_price_to_float(s: str) -> float:
    s = s.strip().replace(" ", "")
    if "," in s and "." not in s:
        return float(s.replace(",", "."))
    if "." in s and "," not in s:
        return float(s)
    if "," in s and "." in s:
        return float(s.replace(".", "").replace(",", "."))
    return float(s)


def _price_from_precio_wrap(block: Any) -> Tuple[Optional[float], Optional[str]]:
    el = block.select_one(".precio_wrap")
    if not el:
        return None, None
    txt = el.get_text(" ", strip=True).replace("\xa0", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return None, None
    low = txt.lower()
    if "agotad" in low:
        return None, "Agotadas"
    if "libre" in low and "€" not in txt:
        return 0.0, "Entrada libre"
    m = re.search(r"(\d+[.,]\d+|\d+)\s*€", txt)
    if m:
        try:
            return _spanish_price_to_float(m.group(1)), txt[:120]
        except ValueError:
            pass
    return None, txt[:120] if txt else None


_PERFORMER_PLACEHOLDERS = re.compile(
    r"^(y\s+más\.?\.?\.?|and\s+more\.?\.?\.?|tba|por\s+confirmar)$",
    re.IGNORECASE,
)


def _parse_page(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    today = date.today()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select('[itemtype*="MusicEvent"]'):
        start_m = block.select_one('meta[itemprop="startDate"]')
        if not start_m or not start_m.get("content"):
            continue
        dt_start = _parse_dt(start_m["content"])
        if not dt_start:
            continue
        d_from = dt_start.date()
        time_text: Optional[str] = None
        if dt_start.hour or dt_start.minute:
            time_text = f"{dt_start.hour:02d}:{dt_start.minute:02d}"

        end_m = block.select_one('meta[itemprop="endDate"]')
        dt_end = _parse_dt(end_m["content"]) if end_m and end_m.get("content") else None
        d_to = dt_end.date() if dt_end else d_from

        if d_to < today:
            continue

        url_m = block.select_one('meta[itemprop="url"]')
        detail = _abs_url(url_m["content"]) if url_m and url_m.get("content") else LIST_URL
        if detail in seen:
            continue

        # Prefer the event-level name over performer/location names.
        # For festivals, the short name lives in <strong class="color2" title="Vive Latino">.
        # For regular events, it's in the first meta[itemprop="name"] that is NOT
        # nested inside a performer/location block (those come after performers in microdatos).
        title_a = block.select_one("a.nombre")
        event_name: Optional[str] = None

        # 1) Festival badge: <strong class="color2" title="Festival Name">
        festival_badge = block.select_one("strong.color2[title]")
        if festival_badge:
            badge_title = (festival_badge.get("title") or "").strip()
            if badge_title:
                event_name = badge_title

        # 2) Fallback: first meta[itemprop="name"] not inside a performer/location
        if not event_name:
            for m in block.select('meta[itemprop="name"]'):
                if m.find_parent(itemprop="performer") or m.find_parent(itemprop="location"):
                    continue
                val = (m.get("content") or "").strip().rstrip(". ")
                if val:
                    event_name = val
                    break

        title = event_name or (title_a.get_text(" ", strip=True) if title_a else None)
        if not title:
            continue

        loc = block.select_one('div[itemprop="location"]')
        venue_name: Optional[str] = None
        if loc:
            vn = loc.select_one('meta[itemprop="name"]')
            if vn and vn.get("content"):
                venue_name = vn["content"].strip()

        venue_slug = _slugify(venue_name) if venue_name else None

        price_eur, price_text = _price_from_precio_wrap(block)

        base_event = {
            "category": CATEGORY,
            "category_slug": CATEGORY_SLUG,
            "venue": venue_name,
            "venue_slug": venue_slug,
            "date_from": d_from,
            "date_to": d_to,
            "time_text": time_text,
            "price_text": price_text,
            "price_min_eur": price_eur,
            "detail_url": detail,
            "source": SOURCE,
        }

        # Collect all performers in this event block.
        performer_divs = block.select('div[itemprop="performer"]')
        performers: List[str] = []
        for p in performer_divs:
            pm = p.select_one('meta[itemprop="name"]')
            if pm and pm.get("content"):
                name = pm["content"].strip()
                if name and not _PERFORMER_PLACEHOLDERS.match(name):
                    performers.append(name)

        seen.add(detail)

        # Always emit the festival/main event itself.
        out.append({"title": title, **base_event})

        # For multi-performer events, also emit one event per artist.
        if len(performers) > 1:
            title_lower = title.strip().lower()
            for performer in performers:
                # Skip if this performer is already represented by the main event title
                # (can happen when the title equals the first performer's name).
                if performer.strip().lower() == title_lower:
                    continue
                out.append({"title": performer, **base_event})

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
        html = _fetch(LIST_URL)
        events = _parse_page(html)
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events
