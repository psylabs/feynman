import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class EventBus:
    """In-process pub-sub. Components emit; sinks consume.

    Two sinks: a JSONL log file (rotated daily) and any number of in-memory
    queues that the SSE endpoint subscribes to.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, type: str, **data: Any) -> None:
        event = {"ts": time.time(), "type": type, **data}
        self._write_log(event)
        for q in list(self._subscribers):
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
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)
