"""
Sliding-window rate limiter — prevents Telegram flood bans.
"""

import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_messages: int = 20, per_seconds: int = 60):
        self.max_messages = max_messages
        self.per_seconds = per_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a send slot is available, then claim it."""
        async with self._lock:
            now = time.monotonic()
            # Drop timestamps outside the window
            while self._timestamps and now - self._timestamps[0] > self.per_seconds:
                self._timestamps.popleft()

            if len(self._timestamps) >= self.max_messages:
                # How long until the oldest message leaves the window
                sleep_for = self.per_seconds - (now - self._timestamps[0]) + 0.1
                logger.warning(f"⏳ Rate limit reached — waiting {sleep_for:.1f}s")
                await asyncio.sleep(sleep_for)
                # Re-purge after sleeping
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] > self.per_seconds:
                    self._timestamps.popleft()

            self._timestamps.append(time.monotonic())
