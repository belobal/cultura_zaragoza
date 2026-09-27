"""Request vs refresh policy for on-disk event caches.

On Render free tier, scrapers often cannot finish a live refresh within the
aggregator timeout. Prefer serving expired disk caches on the request path;
the warm thread can force a live refresh with force_cache_refresh().
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
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


def get_disk_events(
    load_cache: Callable[..., Optional[List[Dict[str, Any]]]],
    ttl_seconds: int,
) -> Optional[List[Dict[str, Any]]]:
    """
    Return fresh cache, or expired disk cache when prefer_stale_cache() is True.
    None means the caller should attempt a live scrape.
    """
    cached = load_cache(ttl_seconds)
    if cached is not None:
        return cached
    if not prefer_stale_cache():
        return None
    return load_cache(STALE_TTL_SECONDS)
