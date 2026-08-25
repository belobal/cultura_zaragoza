import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import os
import requests
from bs4 import BeautifulSoup


SOURCE_NAME = "Rock & Blues Café"
IFRAME_URL = "https://www.sweetcaroline.app/programacion8.php"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "rockandbluescafe_events.json"

DEFAULT_TTL_SECONDS = 60 * 60  # 1h
_CACHE_SCHEMA_VERSION = 4


_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _parse_date_yyyy_mm_dd_from_backoffice(s: str) -> Optional[date]:
    """
    En el iframe aparece en rutas del estilo:
      backoffice/eventos/18-03-2026-CON-V-DE-VOZ/foto.jpg
    """
    if not s:
        return None
    m = re.search(r"(\d{2}-\d{2}-\d{4})", s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d-%m-%Y").date()
    except ValueError:
        return None


def _slugify_category(name: str) -> str:
    name = name.lower().strip()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
        "&": "y",
        "’": "",
    }
    for k, v in replacements.items():
        name = name.replace(k, v)
    name = re.sub(r"[^a-z0-9\\-]", "", name)
    name = re.sub(r"\\-+", "-", name).strip("-")
    return name


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        events = payload.get("events", [])
        for e in events:
            if isinstance(e.get("date_from"), str):
                e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
            if isinstance(e.get("date_to"), str):
                e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
        return events
    except Exception:
        return None
    return None



def _save_cache(events: List[Dict[str, Any]]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.utcnow().isoformat(),
        "schema_version": _CACHE_SCHEMA_VERSION,
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


def _fetch_iframe_html() -> str:
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(IFRAME_URL, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def _extract_events_from_iframe(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")

    # Cada elemento "listing" suele estar dentro de un grid (col-*)
    containers = []
    for col in soup.select("div.col-lg-4, div.col-md-6"):
        h3 = col.find("h3")
        img = col.find("img", attrs={"data-src": True})
        if not h3 or not img:
            continue
        data_src = img.get("data-src") or img.get("src") or ""
        d = _parse_date_yyyy_mm_dd_from_backoffice(data_src)
        if not d:
            continue
        # Try to extract time from the detail page (programacion10.php?idevento=...).
        # In SweetCaroline the time is shown next to the date: "15-04-2026 · 21:00".
        time_text: Optional[str] = None
        try:
            a0 = col.find("a", href=True)
            href0 = a0["href"].strip() if a0 else ""
            if href0:
                if href0.startswith("http"):
                    detail0 = href0
                else:
                    detail0 = f"https://www.sweetcaroline.app/{href0.lstrip('/')}"
                # Only fetch the lightweight internal detail pages; skip external ticketing URLs.
                if "sweetcaroline.app/programacion10.php" in detail0:
                    headers = {
                        "User-Agent": _UA,
                        "Accept-Language": "es-ES,es;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    }
                    r0 = requests.get(detail0, headers=headers, timeout=30)
                    if r0.ok and r0.text:
                        # The separator can be a literal middle dot or its HTML entity.
                        m0 = re.search(
                            r"\b\d{2}-\d{2}-\d{4}\s*(?:·|&middot;|&#183;)\s*(\d{1,2}:\d{2})\b",
                            r0.text,
                        )
                        if m0:
                            hhmm = m0.group(1)
                            # Normalize to HH:MM
                            h, mm = hhmm.split(":", 1)
                            time_text = f"{int(h):02d}:{mm}"
        except Exception:
            time_text = None

        containers.append((col, h3.get_text(" ", strip=True), d, data_src, time_text))

    events: Dict[str, Dict[str, Any]] = {}
    for col, title, d, data_src, time_text in containers:
        # Deducción de precio:
        # - si pone "Acceso Libre" -> 0 EUR
        # - si pone "Con Entrada" -> no hay precio numérico visible en HTML
        text = col.get_text(" ", strip=True).lower()
        price_min_eur: Optional[float] = None
        price_text: Optional[str] = None
        if "acceso libre" in text:
            price_min_eur = 0.0
            price_text = "Acceso Libre"
        elif "con entrada" in text:
            price_min_eur = None
            price_text = "Con Entrada"

        # Enlaces: a veces hay `a` hacia sweetcaroline con la venta
        a = col.find("a", href=True)
        detail_url = a["href"] if a else None
        if detail_url and not detail_url.startswith("http"):
            if detail_url.startswith("/"):
                detail_url = f"https://www.rockandbluescafe.com{detail_url}"
            else:
                # En el iframe suelen venir URLs relativas del tipo `programacion10.php?...`
                # apuntando a SweetCaroline.
                detail_url = f"https://www.sweetcaroline.app/{detail_url}"

        category_name = f"{SOURCE_NAME} (Conciertos)"
        category_slug = _slugify_category(category_name)

        # En la agenda del Rock & Blues Café el recinto es el propio local
        venue_name = "Rock & Blues Café"
        venue_slug = _slugify_category(venue_name)

        # Clave para deduplicar
        key = (detail_url or title) + d.isoformat()
        events[key] = {
            "title": title,
            "category": category_name,
            "category_slug": category_slug,
            "venue": venue_name,
            "venue_slug": venue_slug,
            "date_from": d,
            "date_to": d,
            "time_text": time_text,
            "price_text": price_text,
            "price_min_eur": price_min_eur,
            "detail_url": detail_url or IFRAME_URL,
            "source": "rockandbluescafe",
        }

    # Orden por fecha
    out = list(events.values())
    out.sort(key=lambda x: (x["date_from"], x["title"]))
    return out


def get_events() -> List[Dict[str, Any]]:
    ttl_seconds = int(os.environ.get("EVENT_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
    cached = _load_cache(ttl_seconds)
    if cached is not None:
        # Reparar date_from/date_to a datetime.date
        cleaned: List[Dict[str, Any]] = []
        for e in cached:
            e2 = dict(e)
            e2["date_from"] = datetime.strptime(e2["date_from"], "%Y-%m-%d").date()
            e2["date_to"] = datetime.strptime(e2["date_to"], "%Y-%m-%d").date()
            e2.setdefault("venue", None)
            e2.setdefault("venue_slug", None)
            cleaned.append(e2)
        return cleaned

    html = _fetch_iframe_html()
    events = _extract_events_from_iframe(html)
    _save_cache(events)
    return events

