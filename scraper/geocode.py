import json
import os
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

# Manual pins (override disk cache / Nominatim). Prefer exact venue addresses.
_AUDITORIO = (41.6380127, -0.9008569)
_PLAZA_PILAR = (41.6562964, -0.878942)
_KNOWN_COORDS: Dict[str, Tuple[float, float]] = {
    # Rock & Blues Café — C. del Cuatro de Agosto 5-7, 50003 Zaragoza
    "rock-y-blues-cafe": (41.6533006, -0.8810604),
    "rock-y-blues": (41.6533006, -0.8810604),
    "rock-and-blues-cafe": (41.6533006, -0.8810604),
    "rock-blues-cafe": (41.6533006, -0.8810604),
    # Belushi Club de Comedia — C. de Bernardo Fita 11, 50005 Zaragoza
    "belushi-club-de-comedia": (41.6465870, -0.8906184),
    "belushi": (41.6465870, -0.8906184),
    # Auditorio de Zaragoza (all rooms / aliases)
    "auditorio-de-zaragoza": _AUDITORIO,
    "auditorio-de-zaragoza-princesa-leonor": _AUDITORIO,
    "auditorio-de-zaragoza-sala-mozart": _AUDITORIO,
    "auditorio-de-zaragoza-sala-multiusos": _AUDITORIO,
    "sala-multiusos-del-auditorio": _AUDITORIO,
    "sala-mozart-del-auditorio": _AUDITORIO,
    "sala-mozart-auditorio": _AUDITORIO,
    # Plaza del Pilar / Escenario Ámbar
    "plaza-del-pilar": _PLAZA_PILAR,
    "ayuntamiento-de-zaragoza-plaza-del-pilar": _PLAZA_PILAR,
    "oficina-de-turismo-plaza-del-pilar": _PLAZA_PILAR,
    "escenario-ambar-fuente-de-goya": _PLAZA_PILAR,
    "escenario-ambar---fuente-de-goya": _PLAZA_PILAR,
    "escenario-ambar-fuente-de-goya-plaza-del-pilar": _PLAZA_PILAR,
    # Teatro del Mercado — Plaza Santo Domingo
    "teatro-del-mercado": (41.6571208, -0.8890132),
    # Other frequent venues
    "teatro-de-las-esquinas": (41.6471851, -0.907778),
    "teatro-principal": (41.6520494, -0.8792281),
    "teatro-principal-de-zaragoza": (41.6520494, -0.8792281),
    "teatro-principal-zaragoza": (41.6520494, -0.8792281),
    "sala-oasis-club": (41.6552154, -0.8855983),
    "sala-oasis-club-zaragoza": (41.6552154, -0.8855983),
    "oasis-club-teatro": (41.6552154, -0.8855983),
    "pabellon-principe-felipe": (41.6353666, -0.8662366),
    "pabellon-de-deportes-principe-felipe": (41.6353666, -0.8662366),
    "paraninfo-de-la-universidad-de-zaragoza": (41.6473228, -0.8866786),
    "edificio-paraninfo": (41.6473228, -0.8866786),
    "jardin-de-invierno": (41.6317771, -0.8927026),
    "jardin-de-invierno-parque-jose-antonio-labordeta": (41.6317771, -0.8927026),
    "el-jardin-de-las-artes": (41.670757, -0.9223315),
    "espacio-zity": (41.6197925, -0.937411),
    "espacio-zity-valdespartera": (41.6197925, -0.937411),
    "espacio-zity-recinto-ferial-de-valdespartera": (41.6197925, -0.937411),
    "recinto-ferial-valdespartera": (41.6197925, -0.937411),
    "hotel-vincci-zaragoza-zentro": (41.6508827, -0.8782637),
    "vincci-zaragoza-zentro": (41.6508827, -0.8782637),
    "cupula-geodesica": (41.6345895, -0.8694427),
    "parque-pignatelli": (41.6371365, -0.8851870),
    "gran-hotel-de-zaragoza-nh-collection": (41.6491310, -0.8816177),
    "centro-civico-estacion-del-norte": (41.6601083, -0.8717459),
    "estacion-del-norte": (41.6601083, -0.8717459),
    "la-lata-de-bombillas": (41.654843, -0.878602),
    "sala-lopez": (41.6584954, -0.8748586),
    "sala-lopez-zaragoza": (41.6584954, -0.8748586),
    # El Túnel — Pº María del Carmen Soldevila, s/n (Oliver)
    "el-tunel-centro-de-artes-para-jovenes": (41.6510478, -0.9244044),
    "el-tunel": (41.6510478, -0.9244044),
}

# If a cached slug is null/missing, try these related keys.
_COORD_FALLBACKS: Dict[str, str] = {
    "auditorio-de-zaragoza-princesa-leonor": "auditorio-de-zaragoza",
    "auditorio-de-zaragoza-sala-mozart": "auditorio-de-zaragoza",
    "auditorio-de-zaragoza-sala-multiusos": "auditorio-de-zaragoza",
    "sala-multiusos-del-auditorio": "auditorio-de-zaragoza",
    "pabellon-de-deportes-principe-felipe": "pabellon-principe-felipe",
    "paraninfo-de-la-universidad-de-zaragoza": "edificio-paraninfo",
    "jardin-de-invierno-parque-jose-antonio-labordeta": "jardin-de-invierno",
    "ayuntamiento-de-zaragoza-plaza-del-pilar": "plaza-del-pilar",
    "vincci-zaragoza-zentro": "hotel-vincci-zaragoza-zentro",
    "recinto-ferial-valdespartera": "espacio-zity",
    "escenario-ambar---fuente-de-goya": "escenario-ambar-fuente-de-goya",
}


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

    # Rock & Blues Café — C. del Cuatro de Agosto 5-7, 50003 Zaragoza
    if "rock" in venue_norm.lower() and "blues" in venue_norm.lower():
        queries.insert(0, "Calle del Cuatro de Agosto 5-7, 50003 Zaragoza, España")
        queries.insert(0, "Cuatro de Agosto 5 Zaragoza")
        queries.insert(0, "Rock & Blues, Calle del Cuatro de Agosto, Zaragoza")

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

    # Sala Roze — https://www.aragonmusical.com/lugares/sala-roze/
    if "roze" in venue_norm.lower():
        queries.insert(0, "Calle Cristóbal Colón 16, 50007 Zaragoza, España")
        queries.insert(0, "Cristóbal Colón 16 Zaragoza")

    # Belushi Club de Comedia — C. de Bernardo Fita 11, 50005 Zaragoza
    if "belushi" in venue_norm.lower():
        queries.insert(0, "Calle Bernardo Fita 11, 50005 Zaragoza, España")
        queries.insert(0, "Bernardo Fita 11 Zaragoza")

    # El Túnel — Pº María del Carmen Soldevila, s/n (Oliver)
    if "tunel" in venue_norm.lower():
        queries.insert(0, "Calle María del Carmen Soldevila Menéndez, 50011 Zaragoza, España")
        queries.insert(0, "María del Carmen Soldevila Menéndez Zaragoza")

    # Las Food Trucks / Fiestas del Pilar
    if "san pablo" in venue_norm.lower() and "parque" in venue_norm.lower():
        queries.insert(0, "Parque San Pablo, Zaragoza, España")

    # El Jardín de las Artes (Almozandia) — Camino de Monzalbarba 318
    if "jardin" in venue_norm.lower() and "artes" in venue_norm.lower():
        queries.insert(0, "Camino de Monzalbarba 318, 50011 Zaragoza, España")
        queries.insert(0, "Parque Deportivo Ebro, Camino de Monzalbarba, Zaragoza")

    if "estacion del norte" in venue_norm.lower() or "estación del norte" in venue_norm.lower():
        queries.insert(0, "Centro Cívico Estación del Norte, Zaragoza")

    if "salamero" in venue_norm.lower():
        queries.insert(0, "Plaza Salamero, Zaragoza")

    if "ambar" in venue_norm.lower() or "fuente de goya" in venue_norm.lower():
        queries.insert(0, "Plaza del Pilar, Zaragoza, España")

    if "espacio zity" in venue_norm.lower() or "zity" in venue_norm.lower():
        queries.insert(0, "Recinto Ferial de Valdespartera, Zaragoza")

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
            r = requests.get(_NOMINATIM_URL, params=params, headers=headers, timeout=8)
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
    name_key = _slugify(venue_name)

    def _lookup(k: str) -> Optional[Tuple[float, float]]:
        if k in _KNOWN_COORDS:
            return _KNOWN_COORDS[k]
        fb = _COORD_FALLBACKS.get(k)
        if fb and fb in _KNOWN_COORDS:
            return _KNOWN_COORDS[fb]
        if fb and fb in _MEM_CACHE and _MEM_CACHE[fb] is not None:
            return _MEM_CACHE[fb]
        if k in _MEM_CACHE and _MEM_CACHE[k] is not None:
            return _MEM_CACHE[k]
        return None

    found = _lookup(key) or _lookup(name_key)
    if found is not None:
        return found

    # Cached explicit null: still allow fallbacks above; only then give up.
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

