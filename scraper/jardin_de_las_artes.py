"""
El Jardín de las Artes (Almozandia Teatro) — programación.
https://www.almozandiateatro.es/eljardindelasartes/programacion/
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

URL = "https://www.almozandiateatro.es/eljardindelasartes/programacion/"
ENTRADAS_URL = "https://www.almozandiateatro.es/eljardindelasartes/entradas/"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "jardin_de_las_artes_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "jardin_de_las_artes"
VENUE_NAME = "El Jardín de las Artes"
VENUE_SLUG = "el-jardin-de-las-artes"

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

# "Sábado 12 de septiembre de 17:00 a 20:00 H"
# optional year: "... de 2026 de 17:00 ..."
_DATE_RE = re.compile(
    r"(?P<wd>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+"
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-záéíóúñ]+)"
    r"(?:\s+de\s+(?P<year>\d{4}))?"
    r"(?:\s+de\s+(?P<t1>\d{1,2}:\d{2}))?"
    r"(?:\s+a\s+(?P<t2>\d{1,2}:\d{2}))?",
    re.IGNORECASE,
)


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=12)
    r.raise_for_status()
    return r.text


def _parse_schedule(text: str) -> Tuple[Optional[date], Optional[str]]:
    m = _DATE_RE.search((text or "").strip())
    if not m:
        return None, None
    mon = _MONTHS_ES.get(m.group("month").lower())
    if not mon:
        return None, None
    day = int(m.group("day"))
    year = int(m.group("year")) if m.group("year") else date.today().year
    try:
        d = date(year, mon, day)
    except ValueError:
        return None, None
    # Year omitted: only roll forward near year boundary (e.g. Dec → Jan).
    if not m.group("year") and d < date.today() and mon < date.today().month and mon <= 2:
        try:
            d = date(year + 1, mon, day)
        except ValueError:
            pass
    t1 = m.group("t1")
    time_text = None
    if t1:
        hh, mm = t1.split(":")
        time_text = f"{int(hh):02d}:{mm}"
    return d, time_text


def _section_category(node) -> Tuple[str, str]:
    """Infer category from the nearest preceding section heading."""
    for prev in node.find_all_previous(["h2", "h3"]):
        label = (prev.get_text(" ", strip=True) or "").lower()
        if not label:
            continue
        if "familiar" in label:
            return "Infantil", "infantil"
        if "general" in label:
            return "Teatro", "teatro"
        break
    return "Espectáculos", "espectaculos-en-zaragoza"


def _parse_page(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    seen: set = set()

    boxes = soup.select(".elementor-flip-box")
    nodes = boxes if boxes else []

    # Fallback: any Elementor block that carries an Entradium ticket link.
    if not nodes:
        for a in soup.select('a[href*="entradium.com"]'):
            parent = a.find_parent(class_=re.compile(r"elementor"))
            if parent:
                nodes.append(parent)

    for box in nodes:
        title_el = box.select_one(
            ".elementor-flip-box__layer__title, .elementor-heading-title, h2, h3, h4"
        )
        desc_el = box.select_one(".elementor-flip-box__layer__description")
        title = (title_el.get_text(" ", strip=True) if title_el else "").strip()
        desc = (desc_el.get_text(" ", strip=True) if desc_el else "").strip()
        blob = " ".join(box.get_text(" ", strip=True).split())
        if not title:
            # First non-date chunk as title
            title = blob.split("Sábado")[0].split("Sabado")[0].split("Viernes")[0].strip()
            title = re.split(
                r"\b(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b",
                title,
                flags=re.I,
            )[0].strip()
        if not title or len(title) < 2:
            continue
        if title.lower() in {"compar", "detalles del evento"}:
            continue

        d, time_text = _parse_schedule(desc) if desc else (None, None)
        if d is None:
            d, time_text = _parse_schedule(blob)
        if d is None:
            continue

        a = box.select_one('a[href*="entradium.com"], a[href]')
        detail_url = (a.get("href") or "").strip() if a else URL
        if detail_url and not detail_url.startswith("http"):
            detail_url = URL

        category, category_slug = _section_category(box)
        key = (title.lower(), d.isoformat(), time_text or "", detail_url)
        if key in seen:
            continue
        seen.add(key)

        out.append(
            {
                "title": title,
                "category": category,
                "category_slug": category_slug,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": d,
                "date_to": d,
                "time_text": time_text,
                "price_text": None,
                "price_min_eur": None,
                "detail_url": detail_url or URL,
                "source": SOURCE,
            }
        )

    today = date.today()
    out = [e for e in out if e["date_to"] >= today]
    out.sort(key=lambda e: (e["date_from"], e["title"].lower()))
    return out


def scrape_events_list() -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for url in (URL, ENTRADAS_URL):
        try:
            html = _fetch(url)
        except Exception:
            continue
        for e in _parse_page(html):
            key = (
                e["title"].lower(),
                e["date_from"].isoformat(),
                e.get("time_text") or "",
                e.get("detail_url") or "",
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(e)
    events.sort(key=lambda e: (e["date_from"], e["title"].lower()))
    return events


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        age = (datetime.utcnow() - fetched_at).total_seconds()
        if age > ttl_seconds:
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
    _save_cache(events)
    return events
