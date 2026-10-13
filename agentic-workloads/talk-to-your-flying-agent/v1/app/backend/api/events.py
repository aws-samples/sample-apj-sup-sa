"""Thread-safe event bus for broadcasting events to WebSocket subscribers."""

import queue
import threading
from datetime import datetime

_subscribers: list[queue.Queue] = []
_lock = threading.Lock()
_counter = 0
_counter_lock = threading.Lock()


def _next_id() -> int:
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


def emit(event_type: str, data: dict):
    """Push an event to every subscriber queue."""
    event = {
        "id": _next_id(),
        "type": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    with _lock:
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue):
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass
