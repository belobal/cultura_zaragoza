import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from itertools import groupby
from pathlib import Path
from typing import Callable, DefaultDict, List, Optional, Tuple

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from scraper.conciertos_club import get_events as get_conciertos_club_events
from scraper.creedence import get_events as get_creedence_events
from scraper.geocode import get_venue_coords
from scraper.ibercaja_teatro_principal import get_events as get_ibercaja_teatro_principal_events
from scraper.lalata import get_events as get_lata_events
from scraper.belushi import get_events as get_belushi_events
from scraper.rockandbluescafe import get_events as get_rock_events
from scraper.elcrapula import get_events as get_elcrapula_events
from scraper.zaragoza_cultura import get_events as get_zaragoza_events
from scraper.aragonenvivo import get_events as get_aragonenvivo_events
from scraper.bomboyplatillo import get_events as get_bomboyplatillo_events


def create_app() -> Flask:
    app = Flask(__name__)
    app_root = Path(__file__).resolve().parent
    cache_dir = app_root / "cache"

    @app.template_filter("fmt_mmm_dd_yyyy")
    def _fmt_mmm_dd_yyyy_jinja(d):
        if d is None:
            return ""
        if isinstance(d, date):
            return _fmt_mmm_dd_yyyy(d)
        return str(d)

    @app.template_filter("fmt_long_es")
    def _fmt_long_es_jinja(d):
        if d is None:
            return ""
        if isinstance(d, date):
            return _fmt_long_es(d)
        return str(d)

    @app.get("/favicon.png")
    def favicon_png():
        return send_from_directory(app_root, "favicon.png", mimetype="image/png")

    @app.get("/favicon.ico")
    def favicon_ico():
        # Muchos navegadores piden /favicon.ico por defecto.
        return send_from_directory(app_root, "favicon.png", mimetype="image/png")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/events")
    def api_events():
        all_events = get_events_cached()
        events = _filter_events(all_events, request.args)
        events = _with_venue_coords(events)
        # JSON-safe serialization
        payload = [
            {
                "title": e["title"],
                "category": e["category"],
                "category_slug": e.get("category_slug"),
                "source": e.get("source"),
                "venue": e.get("venue"),
                "venue_slug": e.get("venue_slug"),
                "venue_lat": e.get("venue_lat"),
                "venue_lon": e.get("venue_lon"),
                "date_from": e["date_from"].isoformat(),
                "date_to": e["date_to"].isoformat(),
                "detail_url": e["detail_url"],
                "time_text": (e.get("time_text") or "").strip() or None,
                "price_text": e.get("price_text"),
            }
            for e in events
        ]
        return jsonify(payload)

    @app.get("/api/meta")
    def api_meta():
        """Venues and categories for mobile filters (week horizon optional)."""
        all_events = get_events_cached()
        today = date.today()
        week_to = today + timedelta(days=6)
        return jsonify(
            {
                "categories": _available_categories(all_events),
                "venues": _available_venues_for_select(all_events),
                "week": {
                    "date_from": today.isoformat(),
                    "date_to": week_to.isoformat(),
                },
            }
        )

    @app.get("/m")
    def mobile():
        """Mobile app: cultural events for the next 7 days, filter by place and type."""
        args = request.args.to_dict(flat=False)
        # Default to one-week view unless dates are provided.
        if "date_from" not in request.args and "date_to" not in request.args:
            today = date.today()
            args = dict(args)
            args["date_from"] = [today.isoformat()]
            args["date_to"] = [(today + timedelta(days=6)).isoformat()]
        # Flatten MultiDict-like structure for _parse_filters helpers via a shim.
        class _Args:
            def get(self, key, default=None):
                vals = args.get(key)
                if not vals:
                    return default
                return vals[0] if isinstance(vals, list) else vals

            def getlist(self, key):
                vals = args.get(key)
                if not vals:
                    return []
                if isinstance(vals, list):
                    return vals
                return [vals]

        parsed = _parse_filters(_Args())
        all_events = get_events_cached()
        events = _filter_events(all_events, parsed)
        categories = _available_categories(all_events)
        venues_for_select = _available_venues_for_select(all_events)
        events_by_day = _group_events_by_day(events)
        span_days = (parsed["date_to"] - parsed["date_from"]).days + 1
        # Day chips only for short ranges; long ranges still list events by day below.
        week_days = []
        if span_days <= 14:
            week_days = [
                parsed["date_from"] + timedelta(days=i) for i in range(span_days)
            ]
        days_with_events = {d for d, _ in events_by_day}
        return render_template(
            "mobile.html",
            events=events,
            events_by_day=events_by_day,
            categories=categories,
            venues_for_select=venues_for_select,
            selected_categories=_selected_category_for_ui(parsed["category_slugs"]),
            selected_venues=_selected_venue_for_ui(parsed["venue_slugs"]),
            date_from=parsed["date_from"],
            date_to=parsed["date_to"],
            week_days=week_days,
            days_with_events=days_with_events,
            include_centros_civicos=bool(parsed.get("include_centros_civicos")),
            q=parsed.get("q", ""),
        )

    @app.get("/manifest.webmanifest")
    def web_manifest():
        return send_from_directory(
            app_root / "static",
            "manifest.webmanifest",
            mimetype="application/manifest+json",
        )

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(
            app_root / "static",
            "sw.js",
            mimetype="application/javascript",
        )
        # Allow scope / for the service worker registered from root.
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.get("/.well-known/assetlinks.json")
    def asset_links():
        """Digital Asset Links for Android TWA / PWABuilder APK."""
        return send_from_directory(
            app_root / "static" / ".well-known",
            "assetlinks.json",
            mimetype="application/json",
        )

    @app.get("/")
    def index():
        parsed = _parse_filters(request.args)
        all_events = get_events_cached()
        events = _filter_events(all_events, parsed)
        events = _with_venue_coords(events)
        events_by_day = _group_events_by_day(events)
        categories = _available_categories(all_events)
        venues_for_select = _available_venues_for_select(all_events)
        events_export = _events_export_payload(events)
        export_meta = _build_export_meta(parsed, categories, venues_for_select)
        map_points = _events_map_points(events)
        return render_template(
            "index.html",
            events=events,
            events_by_day=events_by_day,
            events_export=events_export,
            export_meta=export_meta,
            map_points=map_points,
            categories=categories,
            venues_for_select=venues_for_select,
            selected_category=parsed["category_slug"],
            date_from=parsed["date_from"],
            date_to=parsed["date_to"],
            q=parsed.get("q", ""),
            selected_venues=_selected_venue_for_ui(parsed["venue_slugs"]),
            include_centros_civicos=bool(parsed.get("include_centros_civicos")),
        )

    @app.post("/export/html")
    def export_html():
        """Genera un HTML autocontenido con la selección enviada desde el navegador."""
        data = request.get_json(silent=True) or {}
        events = data.get("events")
        meta = data.get("meta")
        if not isinstance(events, list) or not isinstance(meta, dict):
            return jsonify({"ok": False, "error": "invalid_payload"}), 400
        if len(events) > 500:
            return jsonify({"ok": False, "error": "too_many_events"}), 400
        required_meta = ("date_from", "date_to", "category", "venues")
        if not all(k in meta for k in required_meta):
            return jsonify({"ok": False, "error": "invalid_meta"}), 400
        for ev in events:
            if not isinstance(ev, dict) or "date_from" not in ev or "date_to" not in ev:
                return jsonify({"ok": False, "error": "invalid_event"}), 400
        try:
            html = _build_agenda_html(meta, events)
        except (ValueError, TypeError, KeyError):
            return jsonify({"ok": False, "error": "build_failed"}), 400
        return Response(
            html,
            mimetype="text/html; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=agenda-zaragoza.html",
                "Cache-Control": "no-store",
            },
        )

    return app


_EVENTS_CACHE = None

# Hilos para combinar fuentes en paralelo (mucho más rápido en arranque frío).
_AGGREGATOR_MAX_WORKERS = int(os.environ.get("AGGREGATOR_MAX_WORKERS", "8"))

_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
_WEEKDAYS_ES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
_MMM_EN = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _fmt_mmm_dd_yyyy(d: date) -> str:
    """e.g. Apr 22 2026 (English 3-letter month, no comma)."""
    return f"{_MMM_EN[d.month - 1]} {d.day} {d.year}"


_VENUE_ALIASES = {
    # Mismo recinto con variantes según fuente
    "pabellon-de-deportes-principe-felipe": (
        "Pabellón Príncipe Felipe",
        "pabellon-principe-felipe",
    ),
    "pabellon-principe-felipe": (
        "Pabellón Príncipe Felipe",
        "pabellon-principe-felipe",
    ),
    # Auditorio renombrado; tratamos ambos como el mismo lugar
    "auditorio-de-zaragoza": (
        "Auditorio de Zaragoza Princesa Leonor",
        "auditorio-de-zaragoza-princesa-leonor",
    ),
    "auditorio-de-zaragoza-princesa-leonor": (
        "Auditorio de Zaragoza Princesa Leonor",
        "auditorio-de-zaragoza-princesa-leonor",
    ),
    # Sala López (alias con/sin tilde y slug histórico)
    "sala-lopez": (
        "Sala López",
        "sala-lopez-zaragoza",
    ),
    "sala-lopez-zaragoza": (
        "Sala López",
        "sala-lopez-zaragoza",
    ),
    # Sala Oasis (variantes entre fuentes)
    "sala-oasis-club": (
        "Sala Oasis Club",
        "sala-oasis-club-zaragoza",
    ),
    "oasis-club-teatro": (
        "Sala Oasis Club",
        "sala-oasis-club-zaragoza",
    ),
    "sala-oasis-club-zaragoza": (
        "Sala Oasis Club",
        "sala-oasis-club-zaragoza",
    ),
    # Teatro Principal (fuentes con naming distinto)
    "teatro-principal": (
        "Teatro Principal",
        "teatro-principal-zaragoza",
    ),
    "teatro-principal-de-zaragoza": (
        "Teatro Principal",
        "teatro-principal-zaragoza",
    ),
    "teatro-principal-zaragoza": (
        "Teatro Principal",
        "teatro-principal-zaragoza",
    ),
    # Sala Creedence: slug distinto en web propia vs conciertos.club
    "sala-creedence": (
        "Sala Creedence",
        "sala-creedence-zaragoza",
    ),
    "sala-creedence-zaragoza": (
        "Sala Creedence",
        "sala-creedence-zaragoza",
    ),
    # Rock & Blues Café (SweetCaroline / Aragón en Vivo / conciertos.club)
    "rock-y-blues-cafe": (
        "Rock & Blues Café",
        "rock-y-blues-cafe",
    ),
    "rock-y-blues": (
        "Rock & Blues Café",
        "rock-y-blues-cafe",
    ),
    "rock-and-blues-cafe": (
        "Rock & Blues Café",
        "rock-y-blues-cafe",
    ),
    "rock-blues-cafe": (
        "Rock & Blues Café",
        "rock-y-blues-cafe",
    ),
}


# Valor de `?venue=` para filtrar todo el complejo (Mozart, Multiusos, Princesa Leonor, etc.).
AUDITORIO_ZARAGOZA_FILTER_SLUG = "auditorio-de-zaragoza"

# Singular/plural and source-specific concert labels → one filter type.
_CATEGORY_ALIASES = {
    "concierto": ("Conciertos", "conciertos-en-zaragoza"),
    "conciertos": ("Conciertos", "conciertos-en-zaragoza"),
}
_CONCIERTOS_SLUG = "conciertos-en-zaragoza"


def _canonicalize_category(event: dict) -> None:
    """Merge equivalent category labels and ensure source names map to artistic concept categories."""
    slug = (event.get("category_slug") or "").strip().lower()
    if slug in _CATEGORY_ALIASES:
        event["category"], event["category_slug"] = _CATEGORY_ALIASES[slug]
        return
    if slug in ("aragon-en-vivo", "bombo-y-platillo", "zaragozala"):
        event["category"] = "Conciertos"
        event["category_slug"] = _CONCIERTOS_SLUG
        return
    if slug in ("elcrapula", "zaragoza-cultura"):
        event["category"] = "Espectáculos"
        event["category_slug"] = "espectaculos-en-zaragoza"
        return
    cat = (event.get("category") or "").strip().lower()
    if cat in ("concierto", "conciertos"):
        event["category"] = "Conciertos"
        event["category_slug"] = _CONCIERTOS_SLUG


def _normalize_category_filter_slugs(slugs: List[str]) -> List[str]:
    """Map legacy concert slugs to the canonical one used in filters."""
    if not slugs or slugs == ["all"]:
        return ["all"]
    out: List[str] = []
    for s in slugs:
        if s in _CATEGORY_ALIASES or s == "conciertos":
            s = _CONCIERTOS_SLUG
        if s and s not in out:
            out.append(s)
    return out or ["all"]


def _slug_is_auditorio_zaragoza(venue_slug: Optional[str]) -> bool:
    """True si la sala pertenece al Auditorio de Zaragoza (cualquier sala del recinto)."""
    vs = (venue_slug or "").strip()
    return vs.startswith("auditorio-de-zaragoza")


def _selected_venue_for_ui(venue_slugs: List[str]) -> List[str]:
    """Unifica slugs del auditorio para marcar la opción única del desplegable (multi-select)."""
    slugs = [s for s in (venue_slugs or []) if s and s != "all"]
    if not slugs:
        return ["all"]
    normalized: List[str] = []
    has_auditorio = any(_slug_is_auditorio_zaragoza(s) for s in slugs)
    if has_auditorio:
        normalized.append(AUDITORIO_ZARAGOZA_FILTER_SLUG)
    for s in slugs:
        if _slug_is_auditorio_zaragoza(s):
            continue
        if s not in normalized:
            normalized.append(s)
    return normalized


def _selected_category_for_ui(category_slugs: List[str]) -> List[str]:
    """Normalize category selection for multi-select UI."""
    slugs = _normalize_category_filter_slugs(
        [s for s in (category_slugs or []) if s and s != "all"]
    )
    return slugs if slugs and slugs != ["all"] else ["all"]


def _parse_multi_slugs(args, key: str) -> List[str]:
    """Parse repeated and/or comma-separated query values into slug list."""
    raw: List[str] = []
    if hasattr(args, "getlist"):
        raw = [v.strip() for v in args.getlist(key) if v and str(v).strip()]
    else:
        v = (args.get(key) or "").strip()
        raw = [v] if v else []
    out: List[str] = []
    for rv in raw:
        for part in str(rv).split(","):
            s = part.strip()
            if s:
                out.append(s)
    if not out or out == ["all"]:
        return ["all"]
    filtered = [s for s in out if s != "all"]
    return filtered or ["all"]


def _canonicalize_venue(event: dict):
    """Fill missing venue_slug and map known aliases to a canonical sala."""
    vs = (event.get("venue_slug") or "").strip()
    if not vs:
        venue = (event.get("venue") or "").strip()
        if venue:
            vs = _slugify_venue_label(venue)
            event["venue_slug"] = vs
    if not vs:
        return
    target = _VENUE_ALIASES.get(vs)
    if not target:
        return
    event["venue"], event["venue_slug"] = target


def _slugify_venue_label(name: str) -> str:
    """Coherente con slugs de salas en scrapers (para tras limpiar el nombre)."""
    x = (name or "").lower().strip()
    repl = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        " ": "-",
        "&": "y",
    }
    for k, v in repl.items():
        x = x.replace(k, v)
    x = re.sub(r"[^a-z0-9\-]", "", x)
    x = re.sub(r"-+", "-", x).strip("-")
    return x or "unknown"


def _shorten_category_display(e: dict) -> None:
    c = e.get("category")
    if not c or not isinstance(c, str):
        return
    short = re.sub(r"\s+en\s+Zaragoza\s*$", "", c.strip(), flags=re.IGNORECASE).strip()
    if short:
        e["category"] = short


def _shorten_venue_display(e: dict) -> None:
    v = e.get("venue")
    if not v or not isinstance(v, str):
        return
    n = v.strip()
    n2 = re.sub(r",\s*Zaragoza\.?\s*$", "", n, flags=re.IGNORECASE).strip()
    n2 = re.sub(r"\.\s*Zaragoza\s*$", "", n2, flags=re.IGNORECASE).strip()
    if n2 and n2 != n:
        e["venue"] = n2
        e["venue_slug"] = _slugify_venue_label(n2)


def _is_deportes_event(e: dict) -> bool:
    slug = (e.get("category_slug") or "").lower()
    cat = (e.get("category") or "").lower().strip()
    if slug == "deportes-en-zaragoza":
        return True
    return cat == "deportes en zaragoza" or cat.startswith("deportes en zaragoza")


def _is_taquilla_com_event(e: dict) -> bool:
    """
    Taquilla.com listings mix cities poorly (date-first, not Zaragoza-only).
    Drop any event whose detail link points at www.taquilla.com.
    """
    url = (e.get("detail_url") or "").strip().lower()
    return "taquilla.com" in url


def _filter_out_taquilla_com(events: List[dict]) -> List[dict]:
    return [e for e in events if not _is_taquilla_com_event(e)]


def _filter_out_deportes(events: List[dict]) -> List[dict]:
    return [e for e in events if not _is_deportes_event(e)]


def _norm_title_for_dedupe(title: str) -> str:
    t = (title or "").lower()
    for a, b in (
        ("á", "a"),
        ("é", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ú", "u"),
        ("ñ", "n"),
        ("’", "'"),
        ("‘", "'"),
        ("´", "'"),
    ):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Drop common leading articles so "THE FLAMIN GROOVIES" ≈ "FLAMIN GROOVIES"
    t = re.sub(r"^(the|los|las|el|la|un|una)\s+", "", t)
    return t


_SOURCE_PRIORITY = {
    # Prefer the venue's own agenda when the same show appears elsewhere.
    "rockandbluescafe": 0,
    "creedence": 1,
    "lalata": 2,
    "lalata_entradas": 2,
    "belushi": 3,
    "ibercaja_teatro_principal": 3,
    "conciertos_club": 8,
    "aragonenvivo": 9,
    "bomboyplatillo": 10,
}


def _source_priority(e: dict) -> int:
    return _SOURCE_PRIORITY.get((e.get("source") or "").strip(), 5)


def _sala_day_dedupe_key(e: dict) -> tuple:
    """Misma sala + mismo día de inicio + título parecido → duplicado."""
    vs = e.get("venue_slug") or ""
    d = e["date_from"]
    ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
    tt = (e.get("time_text") or "").strip()
    return (vs, ds, tt, _norm_title_for_dedupe(e.get("title", "")))


def _title_date_venue_dedupe_key(e: dict) -> Tuple[str, str, str]:
    """Same day + normalized title + venue → one listing."""
    d = e["date_from"]
    ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
    vs = (e.get("venue_slug") or "").strip().lower()
    if not vs:
        vs = _slugify_venue_label(e.get("venue") or "")
    return (ds, _norm_title_for_dedupe(e.get("title", "")), vs)


def _dedupe_prefer_source_by_title_date_venue(events: List[dict]) -> List[dict]:
    """
    Collapse same title + date + venue, preferring Rock & Blues Café (and other
    high-priority venue sources) over transversal aggregators like Aragón en Vivo.
    """
    best: dict[Tuple[str, str, str], dict] = {}
    order: List[Tuple[str, str, str]] = []
    for e in events:
        key = _title_date_venue_dedupe_key(e)
        # Events without a usable title still pass through once.
        if not key[1]:
            order.append(("__raw__", str(id(e)), ""))
            best[order[-1]] = e
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = e
            order.append(key)
            continue
        if _source_priority(e) < _source_priority(prev):
            best[key] = e
    return [best[k] for k in order if k in best]


def _title_date_dedupe_key(e: dict) -> Tuple[str, str]:
    """Same calendar day + same normalized title (fallback when venue missing)."""
    d = e["date_from"]
    ds = d.isoformat() if hasattr(d, "isoformat") else str(d)
    return (ds, _norm_title_for_dedupe(e.get("title", "")))


def _dedupe_first_by_title_and_date(events: List[dict]) -> List[dict]:
    """Keep first occurrence per title+date (merge order already prefers Rock & Blues)."""
    seen: set[Tuple[str, str]] = set()
    out: List[dict] = []
    for e in events:
        key = _title_date_dedupe_key(e)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _title_simplified_for_similarity(title: str) -> str:
    """
    Produce a simplified title to compare near-duplicates.
    Removes bracketed qualifiers and normalizes separators to catch cases like:
      "BAND (USA)" vs "BAND"
      "BAND - Rock / USA" vs "BAND"
    """
    t = (title or "").strip()
    if not t:
        return ""
    # Drop bracketed qualifiers: "(...)" or "[...]"
    t = re.sub(r"\([^)]{0,80}\)", " ", t)
    t = re.sub(r"\[[^\]]{0,80}\]", " ", t)
    # Keep the left side of common separators (often used for qualifiers)
    # Supports both spaced and unspaced separators (e.g. "A / B", "A/B", "A · B").
    t = re.split(r"\s*[/|·]\s*|\s+[-–—]\s+", t, maxsplit=1)[0]
    # Normalize and remove punctuation
    return _norm_title_for_dedupe(t)


def _titles_near_duplicate(a: str, b: str) -> bool:
    """
    True when titles are almost the same, differing mainly in qualifiers.
    Heuristic: substring match OR high token overlap on simplified forms.
    """
    sa = _title_simplified_for_similarity(a)
    sb = _title_simplified_for_similarity(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    if sa in sb or sb in sa:
        # Guard: require at least 3 chars to avoid noise like "dj"
        return min(len(sa), len(sb)) >= 3
    ta, tb = set(sa.split()), set(sb.split())
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    jacc = inter / union if union else 0.0
    return jacc >= 0.85 and min(len(ta), len(tb)) >= 2


def _dedupe_near_titles_same_venue_day_keep_simplest(events: List[dict]) -> List[dict]:
    """
    If two events share venue + day and their titles are near-duplicates,
    keep only one: prefer Rock & Blues / venue-owned sources, else simplest title.
    Time of day is ignored so SweetCaroline vs Aragón en Vivo still collapse.
    """
    groups: DefaultDict[Tuple[str, str], List[int]] = defaultdict(list)
    for i, e in enumerate(events):
        vs = (e.get("venue_slug") or "").strip()
        d = e.get("date_from")
        ds = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
        if not vs or not ds:
            continue
        groups[(vs, ds)].append(i)

    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        kept: List[int] = []
        for i in idxs:
            if i in drop:
                continue
            ti = events[i].get("title") or ""
            matched = False
            for j in kept:
                tj = events[j].get("title") or ""
                if not _titles_near_duplicate(ti, tj):
                    continue
                # Prefer Rock & Blues (and other venue-owned sources) over aggregators.
                if _source_priority(events[i]) < _source_priority(events[j]):
                    drop.add(j)
                    kept.remove(j)
                    kept.append(i)
                elif _source_priority(events[j]) < _source_priority(events[i]):
                    drop.add(i)
                else:
                    # Choose simplest title among the pair
                    si = _title_simplified_for_similarity(ti)
                    sj = _title_simplified_for_similarity(tj)
                    key_i = (len(si), len(ti.strip()))
                    key_j = (len(sj), len(tj.strip()))
                    if key_i < key_j:
                        drop.add(j)
                        kept.remove(j)
                        kept.append(i)
                    else:
                        drop.add(i)
                matched = True
                break
            if not matched and i not in drop:
                kept.append(i)

    if not drop:
        return events
    return [e for k, e in enumerate(events) if k not in drop]


def _drop_conciertos_club_if_duplicate_sala(events: List[dict]) -> List[dict]:
    """
    Si el mismo concierto (sala + día + título normalizado) viene de otra fuente,
    elimina la copia de conciertos_club.
    """
    groups: DefaultDict[Tuple[str, str, str], List[int]] = defaultdict(list)
    for i, e in enumerate(events):
        groups[_sala_day_dedupe_key(e)].append(i)
    remove: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        group_events = [events[i] for i in idxs]
        has_other = any(ge.get("source") != "conciertos_club" for ge in group_events)
        if not has_other:
            continue
        for i in idxs:
            if events[i].get("source") == "conciertos_club":
                remove.add(i)
    return [e for i, e in enumerate(events) if i not in remove]


def _merge_source_results(chunks: List[List[dict]]) -> List[dict]:
    events: List[dict] = []
    for part in chunks:
        events.extend(part)
    for e in events:
        _canonicalize_venue(e)
        _canonicalize_category(e)
        if e.get("source") in (
            "rockandbluescafe",
            "lalata",
            "lalata_entradas",
            "conciertos_club",
        ):
            e["category"] = "Conciertos en Zaragoza"
            e["category_slug"] = "conciertos-en-zaragoza"
    events = _drop_conciertos_club_if_duplicate_sala(events)
    events = _filter_out_deportes(events)
    events = _filter_out_taquilla_com(events)
    for e in events:
        _shorten_category_display(e)
        _shorten_venue_display(e)
        _canonicalize_venue(e)
    # Same title+date+venue → one row; Rock & Blues own site wins over Aragón en Vivo.
    events = _dedupe_prefer_source_by_title_date_venue(events)
    events = _dedupe_near_titles_same_venue_day_keep_simplest(events)
    for e in events:
        _canonicalize_venue(e)
    return events


def _safe_fetch(fn: Callable[[], List[dict]]) -> List[dict]:
    try:
        out = fn()
        return out if isinstance(out, list) else []
    except Exception:
        return []


def _load_all_sources_parallel() -> List[dict]:
    fetchers: List[Callable[[], List[dict]]] = [
        # Taquilla.com y Zaragozala omitidos.
        get_rock_events,
        get_lata_events,
        get_elcrapula_events,
        get_zaragoza_events,
        get_creedence_events,
        get_ibercaja_teatro_principal_events,
        get_belushi_events,
        get_conciertos_club_events,
        # Extra sources at the end so dedupe keeps earlier sources.
        get_aragonenvivo_events,
        get_bomboyplatillo_events,
    ]
    workers = min(max(1, _AGGREGATOR_MAX_WORKERS), len(fetchers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        chunks = list(ex.map(_safe_fetch, fetchers))
    return _merge_source_results(chunks)


def get_events_cached():
    """
    Pequeña capa de cache en memoria para evitar re-leer el archivo en cada request.
    El scraping real usa cache en disco.
    """
    global _EVENTS_CACHE
    if _EVENTS_CACHE is None:
        if os.environ.get("AGGREGATOR_SEQUENTIAL", "0") == "1":
            events = _merge_source_results(
                [
                    get_rock_events(),
                    get_lata_events(),
                    get_elcrapula_events(),
                    get_zaragoza_events(),
                    get_creedence_events(),
                    get_ibercaja_teatro_principal_events(),
                    get_belushi_events(),
                    get_conciertos_club_events(),
                    # Extra sources at the end so dedupe keeps earlier sources.
                    get_aragonenvivo_events(),
                    get_bomboyplatillo_events(),
                ]
            )
        else:
            events = _load_all_sources_parallel()
        _EVENTS_CACHE = events
    return _EVENTS_CACHE


def _available_categories(events):
    cats = {}
    for e in events:
        slug = e.get("category_slug")
        if slug and slug not in ("aragon-en-vivo", "zaragoza-cultura", "bombo-y-platillo", "elcrapula", "zaragozala"):
            cats[slug] = e["category"]

    # Orden explícito de conceptos artísticos
    ordered_slugs_priority = [
        "conciertos-en-zaragoza",
        "teatro",
        "comedia",
        "espectaculos-en-zaragoza",
        "musical",
        "opera",
        "danza",
        "cine",
        "exposiciones",
        "infantil",
    ]

    def _sort_key(slug):
        if slug in ordered_slugs_priority:
            return (0, ordered_slugs_priority.index(slug))
        return (1, cats.get(slug, "").lower())

    sorted_slugs = sorted(cats.keys(), key=_sort_key)
    return [{"slug": k, "name": cats[k]} for k in sorted_slugs]


def _available_venues_for_select(events: List[dict]) -> List[dict]:
    """
    Lista plana de salas para el desplegable (sin grupos).
    Una entrada «Auditorio de Zaragoza»; incluye salas de centros cívicos.
    """
    venues: dict[str, str] = {}
    has_auditorio = False
    for e in events:
        vs = e.get("venue_slug")
        if not vs:
            continue
        if _slug_is_auditorio_zaragoza(vs):
            has_auditorio = True
            continue
        venues[vs] = e.get("venue") or vs

    rows: List[dict] = []
    if has_auditorio:
        rows.append(
            {"slug": AUDITORIO_ZARAGOZA_FILTER_SLUG, "name": "Auditorio de Zaragoza"}
        )
    for slug, name in venues.items():
        rows.append({"slug": slug, "name": name})
    rows.sort(key=lambda x: (x["name"] or "").lower())
    return rows


def _parse_filters(args):
    today = date.today()
    default_from = today
    # Default range: today through +6 days (one week inclusive), same as /m.
    default_to = today + timedelta(days=6)

    def parse_yyyy_mm_dd(v, default):
        if not v:
            return default
        return datetime.strptime(v, "%Y-%m-%d").date()

    date_from = parse_yyyy_mm_dd(args.get("date_from"), default_from)
    date_to = parse_yyyy_mm_dd(args.get("date_to"), default_to)

    category_slugs = _normalize_category_filter_slugs(_parse_multi_slugs(args, "category"))
    # Desktop UI still uses a single select; expose first slug (or "all").
    category_slug = (
        "all"
        if category_slugs == ["all"]
        else category_slugs[0]
    )
    venue_slugs = _parse_multi_slugs(args, "venue")
    q = (args.get("q") or "").strip()
    include_centros_civicos = (args.get("centros") or "").strip() in {"1", "true", "on", "yes"}

    # Asegurar rango coherente
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    return {
        "date_from": date_from,
        "date_to": date_to,
        "category_slug": category_slug,
        "category_slugs": category_slugs,
        "q": q,
        "venue_slugs": venue_slugs,
        "include_centros_civicos": include_centros_civicos,
    }


def _events_map_points(events: List[dict]) -> List[dict]:
    """Marcadores para Leaflet (solo eventos con coordenadas)."""
    out: List[dict] = []
    for e in events:
        lat, lon = e.get("venue_lat"), e.get("venue_lon")
        if lat is None or lon is None:
            continue
        df, dt = e["date_from"], e["date_to"]
        if df == dt:
            date_text = _fmt_mmm_dd_yyyy(df)
        else:
            date_text = f"{_fmt_mmm_dd_yyyy(df)} a {_fmt_mmm_dd_yyyy(dt)}"
        out.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "title": e.get("title") or "",
                "venue": (e.get("venue") or "").strip(),
                "date_text": date_text,
                "detail_url": (e.get("detail_url") or "").strip(),
            }
        )
    return out


def _events_export_payload(events: List[dict]) -> List[dict]:
    """Datos serializables para exportar a HTML desde el navegador."""
    return [
        {
            "date_from": e["date_from"].isoformat(),
            "date_to": e["date_to"].isoformat(),
            "title": e["title"],
            "venue": (e.get("venue") or "").strip(),
            "detail_url": (e.get("detail_url") or "").strip(),
            "time_text": (e.get("time_text") or "").strip() or None,
            "venue_lat": e.get("venue_lat"),
            "venue_lon": e.get("venue_lon"),
            "price_text": e.get("price_text"),
            "price_min_eur": e.get("price_min_eur"),
        }
        for e in events
    ]


def _parse_iso_date_str(s: str) -> date:
    if not s or not isinstance(s, str):
        raise ValueError("invalid date string")
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _fmt_long_es(d: date) -> str:
    return f"{_WEEKDAYS_ES[d.weekday()]}, {d.day} de {_MONTHS_ES[d.month - 1]} de {d.year}"


def _fmt_date_span_title_es(d1: date, d2: date) -> str:
    """Date filter range for PDF title: Spanish, compact (no weekday)."""
    m1 = _MONTHS_ES[d1.month - 1]
    m2 = _MONTHS_ES[d2.month - 1]
    if d1 == d2:
        return f"{d1.day} de {m1} de {d1.year}"
    if d1.year == d2.year:
        if d1.month == d2.month:
            return f"Del {d1.day} al {d2.day} de {m1} de {d1.year}"
        return f"Del {d1.day} de {m1} al {d2.day} de {m2} de {d1.year}"
    return f"Del {d1.day} de {m1} de {d1.year} al {d2.day} de {m2} de {d2.year}"


def _event_price_display(ev: dict) -> str:
    """Texto de precio para export (prioriza price_text del scraper)."""
    pt = ev.get("price_text")
    if pt is not None and str(pt).strip():
        return str(pt).strip()
    pm = ev.get("price_min_eur")
    if pm is not None:
        try:
            v = float(pm)
            if abs(v - round(v)) < 0.001:
                return f"{int(round(v))} EUR"
            return f"{round(v, 2)} EUR"
        except (TypeError, ValueError):
            pass
    return ""


def _sort_events_for_export(events: List[dict]) -> List[dict]:
    return sorted(events, key=_sort_key_date_venue_title)


def _event_details_line(ev: dict) -> str:
    """Venue · time · price for export rows (day is shown in section header)."""
    parts: List[str] = []
    venue = (ev.get("venue") or "").strip()
    time_text = (ev.get("time_text") or "").strip()
    price_disp = _event_price_display(ev)
    if venue:
        parts.append(venue)
    if time_text:
        parts.append(time_text)
    if price_disp:
        parts.append(price_disp)
    return " · ".join(parts)


def _group_export_events_by_day(events: List[dict]) -> List[dict]:
    """Group export payload (ISO date strings) by day for HTML rendering."""
    sorted_ev = _sort_events_for_export(events)
    groups: List[dict] = []
    for day, chunk in groupby(
        sorted_ev, key=lambda e: _parse_iso_date_str(str(e.get("date_from") or ""))
    ):
        day_events = []
        for ev in chunk:
            day_events.append(
                {
                    "title": (ev.get("title") or "").strip() or "(sin título)",
                    "detail_url": (ev.get("detail_url") or "").strip(),
                    "details_line": _event_details_line(ev),
                }
            )
        groups.append({"day": day, "events": day_events})
    return groups


def _build_agenda_html(meta: dict, events: List[dict]) -> str:
    """Self-contained HTML export for the selected events."""
    df = _parse_iso_date_str(meta["date_from"])
    dt = _parse_iso_date_str(meta["date_to"])
    events_by_day = _group_export_events_by_day(events)
    return render_template(
        "agenda_export.html",
        meta=meta,
        events_by_day=events_by_day,
        events_count=len(events),
        date_span=_fmt_date_span_title_es(df, dt),
        generated_at=datetime.now(),
    )


def _build_export_meta(parsed: dict, categories: List[dict], venues: List[dict]) -> dict:
    """Resumen de filtros para export (PDF / metadatos)."""
    cat_slugs = parsed.get("category_slugs") or [parsed.get("category_slug", "all")]
    if not cat_slugs or cat_slugs == ["all"]:
        cat_name = "Todas"
    else:
        names: List[str] = []
        for slug in cat_slugs:
            found = next((c["name"] for c in categories if c["slug"] == slug), None)
            names.append(found or slug)
        cat_name = ", ".join(names) if names else "Todas"

    vslugs = parsed.get("venue_slugs") or ["all"]
    if not vslugs or vslugs == ["all"]:
        venue_label = "Todas"
    else:
        names: List[str] = []
        # If Auditorio is selected, represent it as a single entry.
        if any(s == AUDITORIO_ZARAGOZA_FILTER_SLUG for s in vslugs):
            names.append("Auditorio de Zaragoza")
        for s in vslugs:
            if s in ("all", AUDITORIO_ZARAGOZA_FILTER_SLUG):
                continue
            found = next((v["name"] for v in venues if v["slug"] == s), None)
            names.append(found or s)
        venue_label = ", ".join([n for n in names if n]) if names else "Todas"

    return {
        "date_from": parsed["date_from"].isoformat(),
        "date_to": parsed["date_to"].isoformat(),
        "category": cat_name,
        "venues": venue_label,
        "q": (parsed.get("q") or "").strip(),
        "centros": bool(parsed.get("include_centros_civicos")),
    }


def _sort_key_date_venue_title(e: dict) -> tuple:
    """
    UI / export: group by day, then by venue (case-insensitive), then title.
    Events with no venue name sort after those with a venue the same day.
    """
    d = e.get("date_from")
    v = (e.get("venue") or "").strip().lower()
    t = (e.get("title") or "").lower()
    if v:
        return (d, 0, v, t)
    return (d, 1, "", t)


def _group_events_by_day(events: List[dict]) -> List[Tuple[date, List[dict]]]:
    """Split an already-sorted list into (date_from, [events that day]) groups."""
    if not events:
        return []
    return [(d, list(g)) for d, g in groupby(events, key=lambda e: e["date_from"])]


def _filter_events(events, args_like):
    # `args_like` puede ser:
    # - un diccionario ya parseado por `_parse_filters` (con `date_from`/`date_to` como `date`)
    # - `request.args` (MultiDict) u otro mapping con `get()`
    if (
        isinstance(args_like, dict)
        and isinstance(args_like.get("date_from"), date)
        and isinstance(args_like.get("date_to"), date)
    ):
        parsed = args_like
    else:
        parsed = _parse_filters(args_like)
    date_from = parsed["date_from"]
    date_to = parsed["date_to"]
    category_slugs = parsed.get("category_slugs") or [parsed.get("category_slug", "all")]
    q = parsed.get("q", "").lower()
    venue_slugs = parsed.get("venue_slugs") or ["all"]
    include_centros_civicos = bool(parsed.get("include_centros_civicos"))

    def overlaps(e):
        df = e["date_from"] if isinstance(e["date_from"], date) else datetime.strptime(str(e["date_from"])[:10], "%Y-%m-%d").date()
        dt = e["date_to"] if isinstance(e["date_to"], date) else datetime.strptime(str(e["date_to"])[:10], "%Y-%m-%d").date()
        return dt >= date_from and df <= date_to

    filtered = [e for e in events if overlaps(e)]
    if category_slugs and category_slugs != ["all"]:
        wanted_cats = set(category_slugs)
        filtered = [e for e in filtered if e.get("category_slug") in wanted_cats]
    if q:
        filtered = [e for e in filtered if q in e["title"].lower()]
    if venue_slugs and venue_slugs != ["all"]:
        wanted = set([s for s in venue_slugs if s and s != "all"])
        if AUDITORIO_ZARAGOZA_FILTER_SLUG in wanted:
            filtered = [
                e
                for e in filtered
                if _slug_is_auditorio_zaragoza(e.get("venue_slug"))
                or (e.get("venue_slug") in wanted)
            ]
        else:
            filtered = [e for e in filtered if e.get("venue_slug") in wanted]

    return sorted(filtered, key=_sort_key_date_venue_title)


def _with_venue_coords(events):
    """
    Enriquece cada evento con `venue_lat`/`venue_lon` usando caché geocoding.
    """
    # Cache local durante esta request para no repetir por evento
    coords_cache = {}
    for e in events:
        vs = e.get("venue_slug")
        if not vs:
            e["venue_lat"] = None
            e["venue_lon"] = None
            continue
        if vs in coords_cache:
            latlon = coords_cache[vs]
        else:
            latlon = get_venue_coords(e.get("venue") or vs, vs)
            coords_cache[vs] = latlon

        if latlon:
            e["venue_lat"] = latlon[0]
            e["venue_lon"] = latlon[1]
        else:
            e["venue_lat"] = None
            e["venue_lon"] = None
    return events


if __name__ == "__main__":
    # Configuración mínima
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("DEBUG", "0") == "1"

    app = create_app()
    app.run(host=host, port=port, debug=debug)

