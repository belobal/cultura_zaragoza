import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests


CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "venue_coords.json"
_CACHE_SCHEMA_VERSION = 2

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_MEM_CACHE: Dict[str, Optional[Tuple[float, float]]] = {}
_LAST_REQUEST_TS: Optional[float] = None


def _slugify(s: str) -> str:
    s = (s or "").lower().strip()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
        "&": "y",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    out = "".join(ch for ch in s if ch.isalnum() or ch in {"-", "_"}).strip("-_")
    out = out.replace("--", "-")
    return out or "unknown"


def _load_cache() -> Dict[str, Optional[Tuple[float, float]]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return {}
        raw = payload.get("coords", {})
        out: Dict[str, Optional[Tuple[float, float]]] = {}
        for k, v in raw.items():
            if v is None:
                out[k] = None
            else:
                out[k] = (float(v["lat"]), float(v["lon"]))
        return out
    except Exception:
        return {}


def _save_cache(coords: Dict[str, Optional[Tuple[float, float]]]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "saved_at": datetime.utcnow().isoformat(),
        "coords": {
            k: (None if v is None else {"lat": v[0], "lon": v[1]}) for k, v in coords.items()
        },
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _respect_rate_limit():
    # Nominatim pide respetar límites. Aquí aplicamos un backoff simple.
    global _LAST_REQUEST_TS
    now = time.time()
    if _LAST_REQUEST_TS is None:
        _LAST_REQUEST_TS = now
        return
    elapsed = now - _LAST_REQUEST_TS
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _LAST_REQUEST_TS = time.time()


def _geocode_nominatim(venue_name: str, query_city: str = "Zaragoza") -> Optional[Tuple[float, float]]:
    def _normalize(s: str) -> str:
        s = (s or "").strip()
        replacements = {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ñ": "n",
        }
        for k, v in replacements.items():
            s = s.replace(k, v)
        s = s.replace("&", "y")
        s = re.sub(r"\\s+", " ", s)
        return s

    import re  # local import: mantener dependencias mínimas

    venue_norm = _normalize(venue_name)
    venue_simple = venue_norm.replace(" sala ", " ").replace(" club", "").strip()

    queries = [
        f"{venue_norm}, {query_city}, España",
        f"{venue_norm}, {query_city}",
        f"{venue_simple}, {query_city}",
        f"{venue_norm} {query_city} España",
        f"{venue_norm}, España",
    ]

    # Dirección conocida (del JSON-LD de Rock & Blues Café)
    if "rock" in venue_norm.lower() and "blues" in venue_norm.lower():
        queries.insert(
            0,
            "Calle Cuatro de Agosto 5-7-9, 50003 Zaragoza, España",
        )
        # Nominatim localiza mejor el nombre de calle si lo pasamos como:
        # "Cuatro de Agosto 5-7-9 Zaragoza"
        queries.insert(0, "Cuatro de Agosto 5-7-9 Zaragoza")

    # Normalmente Nominatim no devuelve "Oasis Teatro Club" directamente,
    # pero sí "Sala Oasis Zaragoza".
    if "oasis" in venue_norm.lower():
        queries.insert(0, "Sala Oasis Zaragoza")

    if "lata" in venue_norm.lower() and "bombillas" in venue_norm.lower():
        queries.insert(0, "Calle Espoz y Mina 19, 50003 Zaragoza, España")
        queries.insert(0, "Espoz y Mina 19 Zaragoza")

    if "lopez" in venue_norm.lower() and "sala" in venue_norm.lower():
        queries.insert(0, "Calle Manifestación 22, 50003 Zaragoza, España")
        queries.insert(0, "Sala López Zaragoza")

    # Bbox amplio para Zaragoza (para validar resultados)
    z_lat_min, z_lat_max = 41.25, 42.05
    z_lon_min, z_lon_max = -1.30, -0.35

    def _inside_zaragoza(lat: float, lon: float) -> bool:
        return z_lat_min <= lat <= z_lat_max and z_lon_min <= lon <= z_lon_max

    params_base = {
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
        "countrycodes": "es",
    }
    headers = {
        # Nominatim recomienda un User-Agent identificable
        "User-Agent": "cultura-zaragoza-flask/1.0 (caching geocoder)",
        "Accept-Language": "es-ES,es;q=0.9",
    }

    def _attempt(use_bounded: bool) -> Optional[Tuple[float, float]]:
        for q in queries:
            params = dict(params_base)
            params["q"] = q
            if use_bounded:
                # viewbox: left,bottom,right,top (zona cercana)
                params["bounded"] = 1
                params["viewbox"] = "-1.05,41.50,-0.60,41.95"
            _respect_rate_limit()
            r = requests.get(_NOMINATIM_URL, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data:
                continue
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            if _inside_zaragoza(lat, lon):
                return (lat, lon)
        return None

    # 1) Primero intentamos con bbox cercano
    res = _attempt(use_bounded=True)
    if res is not None:
        return res
    # 2) Si no hay, reintenta sin bounded y valida que esté en Zaragoza
    return _attempt(use_bounded=False)


def get_venue_coords(venue_name: str, venue_slug: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """
    Devuelve (lat, lon) para un nombre de sala/recinto.
    Usa caché en memoria + caché en disco por `venue_slug`/normalización.
    """
    global _MEM_CACHE
    if not _MEM_CACHE:
        _MEM_CACHE = _load_cache()

    key = venue_slug or _slugify(venue_name)
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]

    # No realizar peticiones HTTP en vivo a Nominatim durante la navegación del usuario
    if os.environ.get("ENABLE_LIVE_GEOCODING", "0") == "1":
        try:
            coords = _geocode_nominatim(venue_name)
        except Exception:
            coords = None
        _MEM_CACHE[key] = coords
        try:
            _save_cache(_MEM_CACHE)
        except Exception:
            pass
        return coords

    _MEM_CACHE[key] = None
    return None

