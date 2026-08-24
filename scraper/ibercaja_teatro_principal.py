"""
Teatro Principal de Zaragoza — catálogo Entradas Ibercaja (Janto).

The branded URL https://compraentradas.ibercaja.es/teatroprincipal/public/janto/main.php
often sits behind Incapsula and returns a stub HTML from simple HTTP clients. The public
listing used here is the same catalogue on entradas.ibercaja.es (recinto TP).
"""

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Same programme as the compraentradas deep link; this host returns full HTML to requests.
BASE_URL = "https://entradas.ibercaja.es"
LIST_URL = f"{BASE_URL}/janto/main.php/?Nivel=Recinto&idRecinto=TP"

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "ibercaja_teatro_principal_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 1

SOURCE = "ibercaja_teatro_principal"
VENUE_NAME = "Teatro Principal de Zaragoza"
VENUE_SLUG = "teatro-principal-de-zaragoza"

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

_DATE_LINE_RE = re.compile(
    r"\b(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo),\s*"
    r"(\d{1,2})\s+"
    r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+"
    r"del\s+(\d{4})\b",
    re.IGNORECASE,
)


def _headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _fetch(url: str) -> str:
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _parse_spanish_listing_date(text: str) -> Optional[date]:
    m = _DATE_LINE_RE.search((text or "").replace("\xa0", " "))
    if not m:
        return None
    day_s, month_s, year_s = m.group(1), m.group(2).lower(), m.group(3)
    mon = _MONTHS_ES.get(month_s)
    if not mon:
        return None
    try:
        return date(int(year_s), mon, int(day_s))
    except ValueError:
        return None


def _slugify_category(name: str) -> str:
    x = (name or "").lower().strip()
    repl = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
    }
    for k, v in repl.items():
        x = x.replace(k, v)
    x = re.sub(r"[^a-z0-9\-]", "", x)
    x = re.sub(r"-+", "-", x).strip("-")
    return x or "otros"


def _normalize_category_label(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return "Otros"
    if t.isupper() and len(t) > 3:
        return t.title()
    return t[0].upper() + t[1:] if t else "Otros"


def _parse_price(card_footer) -> Tuple[Optional[str], Optional[float]]:
    if not card_footer:
        return None, None
    span = card_footer.find("span", class_=lambda c: c and "text-20" in c)
    if not span:
        m = re.search(r"Desde\s*([0-9.,]+)\s*€", card_footer.get_text(" ", strip=True))
        if not m:
            return None, None
        raw = m.group(1)
    else:
        raw = span.get_text(strip=True).replace("€", "").strip()
    normalized = raw.replace(".", "").replace(",", ".")
    try:
        val = float(normalized)
    except ValueError:
        val = None
    return f"Desde {raw}€", val


def _parse_events(html: str) -> List[Dict[str, Any]]:
    if "listadoEventos" not in html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in soup.select("div.event-item"):
        title_a = item.select_one(".card-body a[href^='/compra/']")
        if not title_a:
            continue
        h3 = title_a.find("h3")
        title = h3.get_text(" ", strip=True) if h3 else ""
        href = (title_a.get("href") or "").strip()
        if not title or not href:
            continue

        cat_el = item.select_one(".card-body p.text-uppercase.text-12")
        category = _normalize_category_label(
            cat_el.get_text(" ", strip=True) if cat_el else ""
        )
        # Date is in a sibling paragraph with <b>…, not the uppercase category line.
        date_b = item.select_one(".card-body p.text-12 b")
        date_el = date_b.find_parent("p") if date_b else None
        event_date = _parse_spanish_listing_date(
            date_el.get_text(" ", strip=True) if date_el else ""
        )
        if not event_date:
            continue

        detail_url = urljoin(BASE_URL, href)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        footer = item.select_one(".card-footer")
        price_text, price_min_eur = _parse_price(footer)

        out.append(
            {
                "title": title,
                "category": category,
                "category_slug": _slugify_category(category),
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": event_date,
                "date_to": event_date,
                "price_text": price_text,
                "price_min_eur": price_min_eur,
                "detail_url": detail_url,
                "source": SOURCE,
            }
        )

    today = date.today()
    out = [e for e in out if e["date_to"] >= today]
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
        if (date.today() - fetched_at).days * 86400 > ttl_seconds:
            return None
        events = payload.get("events", [])
        for e in events:
            e["date_from"] = datetime.strptime(e["date_from"], "%Y-%m-%d").date()
            e["date_to"] = datetime.strptime(e["date_to"], "%Y-%m-%d").date()
            e.setdefault("source", SOURCE)
            e.setdefault("venue", VENUE_NAME)
            e.setdefault("venue_slug", VENUE_SLUG)
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
        html = _fetch(LIST_URL)
        events = _parse_events(html)
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return events
