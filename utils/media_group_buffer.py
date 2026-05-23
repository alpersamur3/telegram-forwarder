"""
MediaGroupBuffer — collects album messages (by media_group_id)
and flushes them as a batch after a short delay.

Telegram delivers media group items as separate messages sharing
the same media_group_id. This buffer reassembles them.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Awaitable
from pyrogram.types import Message

logger = logging.getLogger(__name__)

# Wait time for all album parts to arrive (seconds)
FLUSH_DELAY = 0.8


class MediaGroupBuffer:
    def __init__(self, flush_delay: float = FLUSH_DELAY):
        self._delay = flush_delay
        # media_group_id → [Message, ...]
        self._groups: dict[str, list[Message]] = defaultdict(list)
        # media_group_id → asyncio.Task
        self._timers: dict[str, asyncio.Task] = {}
        # Callback: async def cb(messages: list[Message]) -> None
        self._callback: Callable[[list[Message]], Awaitable[None]] | None = None

    def set_callback(self, cb: Callable[[list[Message]], Awaitable[None]]) -> None:
        self._callback = cb

    async def add(self, message: Message) -> None:
        """Add a message to the buffer. Callback fires when the timer expires."""
        gid = message.media_group_id
        self._groups[gid].append(message)

        # Cancel existing timer and restart (sliding window)
        if gid in self._timers:
            self._timers[gid].cancel()

        self._timers[gid] = asyncio.create_task(self._flush_after(gid))

    async def _flush_after(self, gid: str) -> None:
        try:
            await asyncio.sleep(self._delay)
            messages = self._groups.pop(gid, [])
            self._timers.pop(gid, None)

            if not messages:
                return

            # Sort by Telegram message ID for safe ordering
            messages.sort(key=lambda m: m.id)
            logger.debug(f"📦 Media group {gid} flushed: {len(messages)} messages")

            if self._callback:
                await self._callback(messages)

        except asyncio.CancelledError:
            pass  # New message arrived, timer was restarted
        except Exception as e:
            logger.error(f"❌ MediaGroupBuffer flush error: {e}", exc_info=True)
