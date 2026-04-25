import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class EventBus:
    """In-process pub-sub. Components emit; sinks consume.

    Two sinks: a JSONL log file (rotated daily) and any number of in-memory
    asyncio queues that the SSE endpoint subscribes to. emit() is safe to call
    from any thread — it routes queue puts back through the subscriber's loop
    via call_soon_threadsafe.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # list of (loop, queue) tuples so we can dispatch from any thread
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []

    def emit(self, type: str, **data: Any) -> None:
        event = {"ts": time.time(), "type": type, **data}
        self._write_log(event)
        for loop, q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(self._safe_put, q, event)
            except RuntimeError:
                # loop closed — drop silently
                pass

    @staticmethod
    def _safe_put(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def _write_log(self, event: dict) -> None:
        date = datetime.fromtimestamp(event["ts"]).strftime("%Y-%m-%d")
        path = self.log_dir / f"{date}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers = [(l, x) for (l, x) in self._subscribers if x is not q]
