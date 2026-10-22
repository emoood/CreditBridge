"""In-process event bus for the developer console. Events are also kept in a ring buffer."""
import asyncio
import itertools
import time
from collections import deque
from typing import Any

_seq = itertools.count(1)
_buffer: deque[dict[str, Any]] = deque(maxlen=2000)
_subscribers: set[asyncio.Queue] = set()


def emit(kind: str, **data: Any) -> dict[str, Any]:
    """kind: llm_call | tool_call | tool_result | citation | rule | stage | error"""
    event = {"id": next(_seq), "ts": time.time(), "kind": kind, **data}
    _buffer.append(event)
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event


def recent(limit: int = 200) -> list[dict[str, Any]]:
    return list(_buffer)[-limit:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def clear() -> None:
    _buffer.clear()
