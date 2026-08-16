"""Volatile cache barrier for Vantav SKU and merchant metrics.

Uses Redis with a short TTL so the AI Command Center and forecasting logic can
serve near-instant snapshots during peak traffic without hammering PostgreSQL.
"""
import json
import os
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


def _redis_client():
    try:
        import redis
        return redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    except Exception as e:
        logger.warning(f"Redis cache barrier unavailable: {e}")
        return None


def _client():
    # Lazy connection per call keeps startup fast and lets the app boot when Redis is down.
    if not hasattr(_client, "_cached") or _client._cached is None:
        _client._cached = _redis_client()
    return _client._cached


_client._cached = None


def _encode(value: Any) -> str:
    return json.dumps(value)


def _decode(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def get(key: str) -> Optional[Any]:
    r = _client()
    if r is None:
        return None
    try:
        return _decode(r.get(key))
    except Exception as e:
        logger.warning(f"Cache get failed for {key}: {e}")
        return None


def set(key: str, value: Any, ttl: int = 60) -> bool:
    r = _client()
    if r is None:
        return False
    try:
        r.setex(key, ttl, _encode(value))
        return True
    except Exception as e:
        logger.warning(f"Cache set failed for {key}: {e}")
        return False


def delete(key: str) -> bool:
    r = _client()
    if r is None:
        return False
    try:
        r.delete(key)
        return True
    except Exception as e:
        logger.warning(f"Cache delete failed for {key}: {e}")
        return False


def get_or_compute(key: str, compute: Callable[[], Any], ttl: int = 60) -> Any:
    """Look up a cached value and fall back to computing/storing it."""
    cached = get(key)
    if cached is not None:
        return cached
    try:
        value = compute()
    except Exception as e:
        logger.warning(f"Cache compute failed for {key}: {e}")
        raise
    set(key, value, ttl=ttl)
    return value


def sku_metrics_key(merchant_id: str, sku: str) -> str:
    return f"merchant:{merchant_id}:sku:{sku}:metrics"


def get_sku_metrics(merchant_id: str, sku: str, compute: Callable[[], Dict[str, Any]], ttl: int = 60) -> Dict[str, Any]:
    return get_or_compute(sku_metrics_key(merchant_id, sku), compute, ttl=ttl)


def set_sku_metrics(merchant_id: str, sku: str, metrics: Dict[str, Any], ttl: int = 60) -> bool:
    return set(sku_metrics_key(merchant_id, sku), metrics, ttl=ttl)
