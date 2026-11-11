"""Distributed & In-Memory Caching Layer.
Supports Redis (production) with automatic local in-memory TTL cache fallback (development/test).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional


class CacheBackend:
    def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class InMemoryCache(CacheBackend):
    def __init__(self):
        self._store = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        expires_at, val = entry
        if time.time() > expires_at:
            del self._store[key]
            return None
        return val

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class RedisCache(CacheBackend):
    def __init__(self, redis_url: str):
        import redis
        self._client = redis.from_url(redis_url, decode_responses=True)

    def get(self, key: str) -> Optional[Any]:
        try:
            val = self._client.get(key)
            return json.loads(val) if val else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value, default=str))
        except Exception:
            pass

    def delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:
            pass


_CACHE_INSTANCE: Optional[CacheBackend] = None


def get_cache() -> CacheBackend:
    global _CACHE_INSTANCE
    if _CACHE_INSTANCE is None:
        redis_url = os.environ.get("REDIS_URL")
        if redis_url:
            try:
                _CACHE_INSTANCE = RedisCache(redis_url)
            except Exception:
                _CACHE_INSTANCE = InMemoryCache()
        else:
            _CACHE_INSTANCE = InMemoryCache()
    return _CACHE_INSTANCE
