"""
MessageMap — maps source message IDs to destination message IDs.

Use cases:
  • Reply chains: correctly reply to the right message in destination
  • Edit sync: update destination when source is edited
  • Delete sync: delete destination when source is deleted

Structure:
  key   = (source_chat_id, source_msg_id, route_id)
  value = (dest_chat_id, dest_msg_id)

Memory management: when MAX_SIZE is exceeded, oldest entries are evicted (FIFO).
"""

import logging
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

MAX_SIZE = 50_000  # ~4 MB memory usage


class MessageMap:
    def __init__(self, max_size: int = MAX_SIZE):
        self._max = max_size
        # OrderedDict → insertion-order FIFO eviction
        self._map: OrderedDict[tuple, tuple] = OrderedDict()

    # ── Write ────────────────────────────────────────────────────────────────

    def put(
        self,
        src_chat: int,
        src_msg: int,
        route_id: int,
        dst_chat: int,
        dst_msg: int,
    ) -> None:
        key = (src_chat, src_msg, route_id)
        self._map[key] = (dst_chat, dst_msg)
        self._map.move_to_end(key)

        if len(self._map) > self._max:
            oldest = next(iter(self._map))
            del self._map[oldest]

    def put_many(
        self,
        src_chat: int,
        src_msg_ids: list[int],
        route_id: int,
        dst_chat: int,
        dst_msg_ids: list[int],
    ) -> None:
        """For multi-message mapping (e.g. media groups)."""
        for src_id, dst_id in zip(src_msg_ids, dst_msg_ids):
            self.put(src_chat, src_id, route_id, dst_chat, dst_id)

    # ── Read ─────────────────────────────────────────────────────────────────

    def get(
        self,
        src_chat: int,
        src_msg: int,
        route_id: int,
    ) -> Optional[tuple[int, int]]:
        """Returns (dest_chat_id, dest_msg_id) or None if not found."""
        return self._map.get((src_chat, src_msg, route_id))

    def get_all_routes(
        self,
        src_chat: int,
        src_msg: int,
    ) -> list[tuple[int, int, int]]:
        """
        Returns all route mappings for the same source message.
        → [(route_id, dest_chat_id, dest_msg_id), ...]
        """
        results = []
        for (sc, sm, rid), (dc, dm) in self._map.items():
            if sc == src_chat and sm == src_msg:
                results.append((rid, dc, dm))
        return results

    def get_all_routes_by_msg_id(
        self,
        src_msg: int,
    ) -> list[tuple[int, int, int, int]]:
        """
        Returns all route mappings across any chat for a given source message ID.
        → [(src_chat_id, route_id, dest_chat_id, dest_msg_id), ...]
        """
        results = []
        for (sc, sm, rid), (dc, dm) in self._map.items():
            if sm == src_msg:
                results.append((sc, rid, dc, dm))
        return results

    # ── Delete ───────────────────────────────────────────────────────────────

    def delete(self, src_chat: int, src_msg: int, route_id: int) -> bool:
        key = (src_chat, src_msg, route_id)
        if key in self._map:
            del self._map[key]
            return True
        return False

    def __len__(self) -> int:
        return len(self._map)
