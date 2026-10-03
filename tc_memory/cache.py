"""进程内存 TTL 缓存（不落盘，重启即失效——符合插件无状态原则）。"""

import time
from typing import Any


class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if time.monotonic() >= expire_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_sec: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl_sec)
