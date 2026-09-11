"""Tiny in-process SSE hub so the UI can show realtime job/account updates."""

import asyncio
import json
from typing import Dict


class SSEHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: Dict) -> None:
        payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        dead = []
        async with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    dead.append(q)
        for q in dead:
            await self.unsubscribe(q)


async def event_stream(hub: SSEHub):
    """Async generator yielding keep-alive formatted SSE events."""
    q = await hub.subscribe()
    try:
        # Initial comment keeps proxies/browsers from closing an idle stream.
        yield ": connected\n\n"
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=15.0)
                yield item
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        await hub.unsubscribe(q)
