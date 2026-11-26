import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """Bounded TTL cache: LRU eviction + per-entry expiry.

    Memory-safe for long-lived warm Lambda containers.
    """

    def __init__(self, maxsize: int = 256, ttl: int = 60):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        # ttl <= 0 disables caching entirely: every read is a miss, nothing is stored.
        return self.ttl > 0

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        item = self._data.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > self.ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._data[key] = (time.time(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
