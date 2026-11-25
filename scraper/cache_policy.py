"""Request vs refresh policy for on-disk event caches.

On Render free tier, scrapers often cannot finish a live refresh within the
aggregator timeout. Prefer serving expired disk caches on the request path;
the warm thread can force a live refresh with force_cache_refresh().

Past-only caches are never reused: they hide rooms that still have programme
online (e.g. El Sótano Mágico) and make the venue filter look empty.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Callable, Dict, Iterator, List, Optional

_state = threading.local()

# Larger than any realistic cache age; used to re-read disk ignoring TTL.
STALE_TTL_SECONDS = 10**9


def prefer_stale_cache() -> bool:
    """True when callers should return expired disk cache instead of scraping."""
    return not getattr(_state, "force_refresh", False)


@contextmanager
def force_cache_refresh() -> Iterator[None]:
    """Temporarily disable stale-cache short-circuit so scrapers hit the network."""
    prev = getattr(_state, "force_refresh", False)
    _state.force_refresh = True
    try:
        yield
    finally:
        _state.force_refresh = prev


def _as_date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def has_upcoming_events(events: Optional[List[Dict[str, Any]]]) -> bool:
    """True if any event ends today or later."""
    if not events:
        return False
    today = date.today()
    for e in events:
        dt = _as_date(e.get("date_to")) or _as_date(e.get("date_from"))
        if dt is not None and dt >= today:
            return True
    return False


def get_disk_events(
    load_cache: Callable[..., Optional[List[Dict[str, Any]]]],
    ttl_seconds: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Return disk cache when it still has upcoming events.

    - Within TTL and with upcoming rows → return it.
    - Expired but with upcoming rows → return it when prefer_stale_cache().
    - Past-only (or empty) → None so the caller scrapes again.
    """
    cached = load_cache(ttl_seconds)
    if cached is not None:
        if has_upcoming_events(cached):
            return cached
        # Fresh-by-TTL but everything is in the past → refresh.
        return None
    if not prefer_stale_cache():
        return None
    stale = load_cache(STALE_TTL_SECONDS)
    if stale is None or not has_upcoming_events(stale):
        return None
    return stale
