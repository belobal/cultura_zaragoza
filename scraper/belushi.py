"""
Belushi Club de Comedia (Zaragoza).
https://belushicomedia.com/

The homepage embeds a JSON array (`var datos = [...]`) with upcoming events,
including date+time, title, category, and external ticket link.
"""

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

URL = "https://belushicomedia.com/"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "belushi_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "belushi"
VENUE_NAME = "Belushi Club de Comedia"
VENUE_SLUG = "belushi-club-de-comedia"

# Keep a dedicated category so the user can filter comedy easily.
CATEGORY = "Comedia"
CATEGORY_SLUG = "comedia"

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


def _extract_datos_array(html: str) -> List[Dict[str, Any]]:
    if not html:
        return []
    m = re.search(r"var\s+datos\s*=\s*(\[[\s\S]*?\]);", html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _parse_fechahora(s: str) -> tuple[Optional[date], Optional[str]]:
    """
    Belushi uses:
      "24/04/2026 20:00"
    """
    s = (s or "").strip()
    if not s:
        return None, None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})", s)
    if not m:
        return None, None
    try:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None, None
    time_text = f"{int(m.group(4)):02d}:{m.group(5)}"
    return d, time_text


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
        html = _fetch(URL)
        rows = _extract_datos_array(html)
    except Exception:
        rows = []

    out: List[Dict[str, Any]] = []
    seen = set()
    today = date.today()
    for r in rows:
        if not isinstance(r, dict):
            continue
        d, time_text = _parse_fechahora(r.get("fechahora") or "")
        if not d or d < today:
            continue
        title = (r.get("titulo") or "").strip() or (r.get("artista") or "").strip()
        if not title:
            continue
        # Prefer external ticket link if present; otherwise point to homepage.
        detail_url = (r.get("enlace") or "").strip() or URL
        key = (title.lower().strip(), d.isoformat(), time_text or "", detail_url)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": d,
                "date_to": d,
                "time_text": time_text,
                "price_text": None,
                "price_min_eur": None,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )
    out.sort(key=lambda e: (e["date_from"], e.get("time_text") or "", e["title"]))
    if out:
        _save_cache(out)
    return out

