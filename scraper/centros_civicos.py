import json
import os
import re
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

BASE = "https://www.zaragoza.es"
LIST_BASE_URL = f"{BASE}/sede/portal/centroscivicos/servicio/cultura/evento/list"
LIST_QUERY = {"idPortal": "7"}

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "centros_civicos_events.json"
DEFAULT_TTL_SECONDS = 60 * 60
_CACHE_SCHEMA_VERSION = 2
# Misma ventana que el filtro por defecto de la web (hoy → +90 días).
_DEFAULT_HORIZON_DAYS = 90

SOURCE = "centros_civicos"
CATEGORY = "Centros Cívicos (Música y Teatro)"
CATEGORY_SLUG = "centros-civicos-musica-y-teatro"

_THEMES_ALLOWED = {"musica", "teatro y artes escenicas"}

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _headers() -> Dict[str, str]:
    return {"User-Agent": _UA, "Accept-Language": "es-ES,es;q=0.9"}


def _fetch(url: str, params: Optional[Dict[str, str]] = None) -> str:
    r = requests.get(url, params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.text


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    repl = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"\s+", " ", s)
    return s


def _slugify(s: str) -> str:
    s = _norm_text(s).replace("&", "y").replace(" ", "-")
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def _extract_page_starts(first_html: str) -> List[int]:
    soup = BeautifulSoup(first_html, "html.parser")
    starts = {0}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "start=" not in href:
            continue
        m = re.search(r"start=(\d+)", href)
        if m:
            starts.add(int(m.group(1)))
    return sorted(starts)


def _extract_event_urls(list_html: str) -> List[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    urls: Set[str] = set()
    for li in soup.select("li[typeof*='Event'][about]"):
        about = li.get("about") or ""
        if "/sede/servicio/cultura/evento/" in about:
            urls.add(about)
    if not urls:
        # fallback
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/sede/servicio/cultura/evento/" in href:
                urls.add(href)
    return sorted(urls)


def _facet_values(html: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    values: Set[str] = set()

    for a in soup.find_all("a", attrs={"data-href": True}):
        dh = a.get("data-href") or ""
        if "fq=" not in dh:
            continue
        label = a.get_text(" ", strip=True)
        if label:
            values.add(label)
        for token in re.findall(r'"([^"]+)"', urllib.parse.unquote(dh)):
            values.add(token.strip())
    return {v for v in values if v}


def _is_allowed_theme(values: Set[str]) -> bool:
    nvals = {_norm_text(v) for v in values}
    return any(v in _THEMES_ALLOWED for v in nvals)


_COURSES_WORKSHOPS_RE = re.compile(r"\b(curso|cursos|taller|talleres)\b", re.IGNORECASE)


def _is_course_or_workshop(title: str, facets: Set[str]) -> bool:
    """
    Centros Cívicos publishes many non-show items (courses/workshops).
    Exclude them from the cultural agenda.
    """
    nt = _norm_text(title or "")
    if _COURSES_WORKSHOPS_RE.search(nt):
        return True
    # Fallback: sometimes the classification appears only in facet labels.
    joined = " ".join(sorted({_norm_text(v) for v in (facets or set())}))
    return bool(_COURSES_WORKSHOPS_RE.search(joined))


def _parse_dates(start_raw: str, end_raw: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
    try:
        d1 = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            d1 = datetime.strptime(start_raw[:10], "%Y-%m-%d").date()
        except Exception:
            return None, None
    if not end_raw:
        return d1, d1
    try:
        d2 = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            d2 = datetime.strptime(end_raw[:10], "%Y-%m-%d").date()
        except Exception:
            d2 = d1
    return d1, d2


def _event_from_detail(html: str, detail_url: str) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    event_data = None
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.get_text())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event" and data.get("name") and data.get("startDate"):
            event_data = data
            break
    if not event_data:
        return None

    facets = _facet_values(html)
    if not _is_allowed_theme(facets):
        return None

    title = str(event_data.get("name", "")).strip()
    if _is_course_or_workshop(title, facets):
        return None
    d1, d2 = _parse_dates(event_data.get("startDate"), event_data.get("endDate"))
    if not title or not d1 or not d2:
        return None

    venue = None
    loc = event_data.get("location")
    if isinstance(loc, list) and loc:
        venue = (loc[0] or {}).get("name")
    elif isinstance(loc, dict):
        venue = loc.get("name")
    venue_slug = _slugify(venue) if venue else None

    return {
        "title": title,
        "category": CATEGORY,
        "category_slug": CATEGORY_SLUG,
        "venue": venue,
        "venue_slug": venue_slug,
        "date_from": d1,
        "date_to": d2,
        "price_text": None,
        "price_min_eur": None,
        "detail_url": detail_url,
        "source": SOURCE,
    }


def _horizon_days() -> int:
    return int(os.environ.get("CENTROS_CIVICOS_HORIZON_DAYS", str(_DEFAULT_HORIZON_DAYS)))


def _overlaps_horizon(e: Dict[str, Any], ref: date, horizon_end: date) -> bool:
    """Solapa [ref, horizon_end] (inclusive en fechas de evento)."""
    return e["date_to"] >= ref and e["date_from"] <= horizon_end


def _filter_horizon(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ref = date.today()
    end = ref + timedelta(days=_horizon_days())
    out = [e for e in events if _overlaps_horizon(e, ref, end)]
    out.sort(key=lambda x: (x["date_from"], x["title"]))
    return out


def scrape_events_list() -> List[Dict[str, Any]]:
    first = _fetch(LIST_BASE_URL, LIST_QUERY)
    starts = _extract_page_starts(first)

    urls: Set[str] = set(_extract_event_urls(first))
    for start in starts:
        if start == 0:
            continue
        html = _fetch(LIST_BASE_URL, {"idPortal": "7", "start": str(start)})
        urls.update(_extract_event_urls(html))

    events: List[Dict[str, Any]] = []
    today = date.today()
    horizon_end = today + timedelta(days=_horizon_days())
    for url in sorted(urls):
        try:
            dhtml = _fetch(url)
        except Exception:
            continue
        e = _event_from_detail(dhtml, url)
        if not e:
            continue
        if not _overlaps_horizon(e, today, horizon_end):
            continue
        events.append(e)

    events.sort(key=lambda x: (x["date_from"], x["title"]))
    return events


def _load_cache(ttl_seconds: int) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        fetched_at = datetime.fromisoformat(payload["fetched_at"]).date()
        if (date.today() - fetched_at).days * 86400 <= ttl_seconds:
            events = payload["events"]
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
        return _filter_horizon(cached)
    try:
        events = scrape_events_list()
    except Exception:
        events = []
    if events:
        _save_cache(events)
    return _filter_horizon(events)

