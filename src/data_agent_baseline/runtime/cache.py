from __future__ import annotations

from collections import OrderedDict, defaultdict
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any


def _now() -> float:
    return monotonic()


class ThreadSafeTTLCache:
    """Small thread-safe TTL + LRU cache without external dependencies."""

    def __init__(self, *, maxsize: int, ttl_seconds: float) -> None:
        self.maxsize = max(int(maxsize), 1)
        self.ttl_seconds = max(float(ttl_seconds), 0.0)
        self._lock = Lock()
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._namespace_index: dict[str, set[str]] = defaultdict(set)
        self.hits = 0
        self.misses = 0

    def _is_expired(self, expires_at: float) -> bool:
        if self.ttl_seconds <= 0:
            return True
        return _now() >= expires_at

    def _purge_expired_locked(self) -> None:
        expired_keys: list[str] = []
        for key, (expires_at, _value) in self._data.items():
            if self._is_expired(expires_at):
                expired_keys.append(key)
        for key in expired_keys:
            self._data.pop(key, None)
            for keys in self._namespace_index.values():
                keys.discard(key)

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if self._is_expired(expires_at):
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any, *, namespace: str | None = None) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            self._purge_expired_locked()
            self._data[key] = (_now() + self.ttl_seconds, value)
            self._data.move_to_end(key)
            if namespace:
                self._namespace_index[namespace].add(key)
            while len(self._data) > self.maxsize:
                oldest_key, _ = self._data.popitem(last=False)
                for keys in self._namespace_index.values():
                    keys.discard(oldest_key)

    def clear_namespace(self, namespace: str) -> None:
        with self._lock:
            keys = self._namespace_index.pop(namespace, set())
            for key in keys:
                self._data.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._data.clear()
            self._namespace_index.clear()


def file_fingerprint(path: Path) -> str:
    """Includes path + size + mtime_ns to invalidate cache on data change."""
    resolved = path.resolve()
    stat = resolved.stat()
    return f"{resolved.as_posix()}|{stat.st_size}|{stat.st_mtime_ns}"


def context_namespace(context_root: Path) -> str:
    return context_root.resolve().as_posix()


# Dedicated caches
SCHEMA_CACHE = ThreadSafeTTLCache(maxsize=512, ttl_seconds=20 * 60)
SQL_RESULT_CACHE = ThreadSafeTTLCache(maxsize=512, ttl_seconds=10 * 60)
EMBEDDING_CACHE = ThreadSafeTTLCache(maxsize=4096, ttl_seconds=60 * 60)
RETRIEVAL_CACHE = ThreadSafeTTLCache(maxsize=1024, ttl_seconds=20 * 60)


def clear_task_caches(context_root: Path) -> None:
    ns = context_namespace(context_root)
    SCHEMA_CACHE.clear_namespace(ns)
    SQL_RESULT_CACHE.clear_namespace(ns)
    RETRIEVAL_CACHE.clear_namespace(ns)

