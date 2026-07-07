"""Shared HTTP + cache helper for all api_clients/* modules.

Every external call should go through `cached_get`, which checks db.api_cache
first and only hits the network on a miss/expiry. Network or HTTP errors are
caught and logged - callers get `None` back rather than an exception, so a
flaky/unavailable data source degrades a dashboard section instead of
crashing the app.
"""

import hashlib
import json
import logging

import requests

import db

logger = logging.getLogger("talent_tracker.api_clients")

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2


def _make_cache_key(url: str, params: dict) -> str:
    normalized = json.dumps(params or {}, sort_keys=True)
    raw = f"{url}?{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


def cached_get(source: str, url: str, params: dict | None = None,
                headers: dict | None = None, ttl_seconds: int = 86400) -> dict | None:
    """Returns parsed JSON dict, or None if unavailable (cache miss + request failed)."""
    cache_key = _make_cache_key(url, params or {})

    cached = db.cache_get(source, cache_key)
    if cached is not None:
        return cached

    last_error = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                db.cache_set(source, cache_key, data, ttl_seconds)
                return data
            if response.status_code in (401, 403):
                logger.warning("%s: auth rejected (HTTP %s) - check API key", source, response.status_code)
                return None
            if response.status_code == 429:
                logger.warning("%s: rate limited (HTTP 429)", source)
                return None
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
    logger.warning("%s: request failed after retries (%s)", source, last_error)
    return None
