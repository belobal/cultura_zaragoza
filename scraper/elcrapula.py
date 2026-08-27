"""
Scraper de eventos desde El Refugio del Crápula.
https://www.elcrapula.es/
"""

import json
import os
import re
from datetime import date, datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

EVENTS_URL = "https://www.elcrapula.es/lib/events.php"
BASE_URL = "https://www.elcrapula.es/"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "elcrapula_events.json"

_CACHE_SCHEMA_VERSION = 1
SOURCE = "elcrapula"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-ES,es;q=0.9",
    }


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    t = html_unescape(text)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
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


def scrape_events() -> List[Dict[str, Any]]:
    r = requests.get(EVENTS_URL, headers=_headers(), timeout=15)
    r.raise_for_status()
    raw_list = r.json()

    events: List[Dict[str, Any]] = []

    for raw in raw_list:
        if not isinstance(raw, dict):
            continue

        start_str = raw.get("start")
        if not start_str:
            continue

        try:
            start_dt = datetime.strptime(start_str.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                start_dt = datetime.strptime(start_str.strip()[:10], "%Y-%m-%d")
            except ValueError:
                continue

        title = _clean_text(raw.get("titulo") or raw.get("title"))
        if not title:
            continue

        cat_raw = (raw.get("categoria") or "").upper()
        if any(k in cat_raw for k in ("MONOLOGO", "HUMOR", "COMEDIA", "PODCAST")):
            category = "Comedia"
            category_slug = "comedia"
        elif "MUSICA" in cat_raw:
            category = "Conciertos"
            category_slug = "conciertos-en-zaragoza"
        elif any(k in cat_raw for k in ("MAGIA", "TEATRO", "ESPECTACULO")):
            category = "Espectáculos"
            category_slug = "espectaculos-en-zaragoza"
        else:
            category = "El Refugio del Crápula"
            category_slug = "elcrapula"

        time_text = start_dt.strftime("%H:%M") if start_dt.hour or start_dt.minute else None

        precioa = str(raw.get("precioa") or "0").strip()
        preciot = str(raw.get("preciot") or "0").strip()
        if precioa in ("0", "0.00", "") and preciot in ("0", "0.00", ""):
            price_text = "Taquilla inversa"
            price_min_eur = 0.0
        elif precioa == preciot:
            price_text = f"{precioa}€"
            try:
                price_min_eur = float(precioa)
            except ValueError:
                price_min_eur = None
        else:
            price_text = f"{precioa}€ ant. / {preciot}€ taq."
            try:
                price_min_eur = float(precioa)
            except ValueError:
                price_min_eur = None

        detail_url = (raw.get("enlace") or "").strip()
        if not detail_url.startswith("http"):
            detail_url = BASE_URL

        events.append(
            {
                "title": title,
                "category": category,
                "category_slug": category_slug,
                "date_from": start_dt.date(),
                "date_to": start_dt.date(),
                "time_text": time_text,
                "price_text": price_text,
                "price_min_eur": price_min_eur,
                "detail_url": detail_url,
                "venue": "El Refugio del Crápula",
                "venue_slug": "el-refugio-del-crapula",
                "source": SOURCE,
            }
        )

    return events


def get_events() -> List[Dict[str, Any]]:
    ttl_seconds = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", "3600"))
    cached = _load_cache(ttl_seconds)
    if cached is not None:
        return cached

    try:
        events = scrape_events()
        _save_cache(events)
        return events
    except Exception:
        return _load_cache(99999999) or []
