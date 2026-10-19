"""Small thread-safe in-process TTL cache for expensive Graph lookups.

Per-process only (each gunicorn worker has its own); a miss just recomputes.
"""
import threading
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, max_items: int = 2000) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expires, value = item
            if expires < time.monotonic():
                del self._data[key]
                return default
            return value

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                # Drop the entry closest to expiry
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (time.monotonic() + self._ttl, value)


_MISSING = object()
