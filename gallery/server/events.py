"""In-process fan-out for the Server-Sent Events stream."""
from __future__ import annotations

import queue
import threading

CLOSE = object()


class Bus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, name: str, data) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((name, data))
            except queue.Full:
                pass  # a stalled client misses events; it can refetch /api/images

    def close(self) -> None:
        with self._lock:
            subs, self._subs = self._subs, []
        for q in subs:
            try:
                q.put_nowait(CLOSE)
            except queue.Full:
                pass

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)
