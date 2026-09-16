"""
Las Food Trucks — Fiestas del Pilar (Parque San Pablo).

Events come from the local JSON schedule (`scraper/foodtrucks.json`).
Times are TBD until confirmed; we surface that explicitly.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_FILE = Path(__file__).resolve().parent / "foodtrucks.json"

SOURCE = "foodtrucks"
VENUE_NAME = "Parque San Pablo"
VENUE_SLUG = "parque-san-pablo"

# Artistic filter: most acts are concerts / live music during Pilar.
CATEGORY = "Conciertos"
CATEGORY_SLUG = "conciertos-en-zaragoza"

_TIPO_LABEL = {
    "concierto": "Concierto",
    "tributo": "Tributo",
    "dj": "DJ",
    "dj_set": "DJ set",
    "dj_party": "DJ party",
    "vermu": "Vermú",
    "vermu_jotero": "Vermú jotero",
    "tardeo": "Tardeo",
    "tardeo_canalla": "Tardeo canalla",
}


def _parse_iso_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _title_for_act(act: Dict[str, Any]) -> str:
    artist = (act.get("artista") or "").strip()
    if not artist:
        return "Actuación"
    tipo = (act.get("tipo") or "").strip().lower()
    parts = [artist]
    if tipo == "tributo" and act.get("tributo_a"):
        parts.append(f"(tributo a {act['tributo_a']})")
    elif tipo in _TIPO_LABEL and tipo not in ("concierto",):
        parts.append(f"({_TIPO_LABEL[tipo]})")
    if act.get("programa"):
        parts.append(f"· {act['programa']}")
    if act.get("patrocinador"):
        parts.append(f"· {act['patrocinador']}")
    return " ".join(parts)


def _load_payload() -> Dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def scrape_events_list() -> List[Dict[str, Any]]:
    payload = _load_payload()
    evento = payload.get("evento") or {}
    acceso = (evento.get("acceso") or "").strip().lower()
    price_text = "Gratuito" if acceso in ("gratuito", "gratis", "free") else None

    events: List[Dict[str, Any]] = []
    for act in payload.get("actuaciones") or []:
        if not isinstance(act, dict):
            continue
        d = _parse_iso_date(act.get("fecha") or "")
        if not d:
            continue
        title = _title_for_act(act)
        events.append(
            {
                "title": title,
                "category": CATEGORY,
                "category_slug": CATEGORY_SLUG,
                "venue": VENUE_NAME,
                "venue_slug": VENUE_SLUG,
                "date_from": d,
                "date_to": d,
                # Schedule times not confirmed yet.
                "time_text": "Por determinar",
                "price_text": price_text,
                "price_min_eur": 0.0 if price_text else None,
                "detail_url": None,
                "source": SOURCE,
            }
        )

    events.sort(key=lambda e: (e["date_from"], e["title"].lower()))
    return events


def get_events() -> List[Dict[str, Any]]:
    try:
        return scrape_events_list()
    except Exception:
        return []
