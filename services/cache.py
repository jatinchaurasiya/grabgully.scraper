"""
services/cache.py
─────────────────
Redis caching via Upstash REST API.
Wraps all cache ops with proper error handling — cache misses are never fatal.
TTL strategy:
  - Deal listings: 15 min (fresh enough, saves scrape quota)
  - Price history: 5 min  (users expect near-real-time)
  - Search results: 5 min
  - Rate limiting: per-window (see rate_limiter)
"""
from __future__ import annotations
import json
from typing import Optional, Any
import httpx
from core.config import get_settings
from core.logger import get_logger

log = get_logger("cache")

# TTL constants (seconds)
TTL_DEALS        = 900   # 15 min
TTL_PRICE_HISTORY = 300  # 5 min
TTL_SEARCH       = 300   # 5 min
TTL_COMPARE      = 600   # 10 min


class RedisCache:
    """
    Upstash Redis via REST API (no persistent TCP connection needed —
    works perfectly in serverless / Railway container).
    """

    def __init__(self):
        s = get_settings()
        self._url   = s.upstash_redis_url.rstrip("/")
        self._token = s.upstash_redis_token
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    async def _req(self, *cmd: str) -> Any:
        """Execute a single Redis command via Upstash REST."""
        url = f"{self._url}/{'/'.join(str(c) for c in cmd)}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            body = r.json()
            return body.get("result")

    async def get(self, key: str) -> Optional[Any]:
        try:
            raw = await self._req("GET", key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            log.warning("cache_get_failed", key=key, error=str(e))
            return None

    async def set(self, key: str, value: Any, ttl: int = TTL_DEALS) -> bool:
        try:
            payload = json.dumps(value, default=str)
            await self._req("SET", key, payload, "EX", ttl)
            return True
        except Exception as e:
            log.warning("cache_set_failed", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> None:
        try:
            await self._req("DEL", key)
        except Exception as e:
            log.warning("cache_delete_failed", key=key, error=str(e))

    async def incr(self, key: str, ttl: int = 60) -> int:
        """Increment a counter, set TTL if key is new. Used for rate limiting."""
        try:
            val = await self._req("INCR", key)
            # Set expiry on first increment
            if val == 1:
                await self._req("EXPIRE", key, ttl)
            return int(val)
        except Exception as e:
            log.warning("cache_incr_failed", key=key, error=str(e))
            return 0

    async def get_or_set(self, key: str, factory, ttl: int = TTL_DEALS) -> Any:
        """
        Cache-aside pattern.
        Try cache first; if miss, call factory(), store result, return it.
        """
        cached = await self.get(key)
        if cached is not None:
            log.debug("cache_hit", key=key)
            return cached
        log.debug("cache_miss", key=key)
        value = await factory()
        if value is not None:
            await self.set(key, value, ttl)
        return value


_cache: Optional[RedisCache] = None


def get_cache() -> RedisCache:
    global _cache
    if _cache is None:
        _cache = RedisCache()
    return _cache
