"""
Configuration & persistent route storage.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROUTES_FILE = Path("data/routes.json")


@dataclass
class Route:
    id: int
    source_chat_id: int          # Channel/group ID (negative)
    source_topic_id: Optional[int]  # None = all topics / main chat
    dest_chat_id: int
    dest_topic_id: Optional[int]    # None = main chat / no topic
    filters_text: list[str] = field(default_factory=list)  # keyword filters
    enabled: bool = True

    def matches(self, chat_id: int, topic_id: Optional[int]) -> bool:
        if self.source_chat_id != chat_id:
            return False
        if self.source_topic_id is None:
            return True           # forward everything from this chat
        
        # General topic normalization: in Telegram forums, both 0/None and 1 represent the General topic.
        src_norm = 1 if self.source_topic_id in (0, 1) else self.source_topic_id
        incoming_norm = 1 if (topic_id or 0) in (0, 1) else topic_id
        return src_norm == incoming_norm


class Settings:
    # ── Telegram credentials ────────────────────────────────────────────────
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN")          # leave empty for userbot
    SESSION_NAME: str = os.getenv("SESSION_NAME", "forwarder")

    # ── Admin ───────────────────────────────────────────────────────────────
    ADMIN_IDS: list[int] = [
        int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
    ]

    # ── Anti-ban rate limiting ───────────────────────────────────────────────
    RATE_LIMIT_MESSAGES: int = int(os.getenv("RATE_LIMIT_MESSAGES", "20"))
    RATE_LIMIT_SECONDS: int = int(os.getenv("RATE_LIMIT_SECONDS", "60"))
    # Extra delay between each forward (seconds)
    FORWARD_DELAY: float = float(os.getenv("FORWARD_DELAY", "0.5"))

    # ── Forwarding behaviour ─────────────────────────────────────────────────
    # "forward"  → uses Telegram's native forward (shows "Forwarded from …")
    # "copy"     → sends a copy (no "Forwarded from" header, avoids some restrictions)
    FORWARD_MODE: str = os.getenv("FORWARD_MODE", "copy")
    FORWARD_SERVICE_MESSAGES: bool = os.getenv("FORWARD_SERVICE_MESSAGES", "False").strip().lower() in ("true", "1", "yes")

    # ── Content filtering ───────────────────────────────────────────────────
    # Whitelist of allowed types (e.g. text, photo, video, document, animation, poll, sticker)
    # If empty, all types are allowed.
    ALLOWED_MEDIA_TYPES: list[str] = [
        x.strip().lower() for x in os.getenv("ALLOWED_MEDIA_TYPES", "").split(",") if x.strip()
    ]
    # Blacklist of blocked types (e.g. sticker, poll)
    BLOCKED_MEDIA_TYPES: list[str] = [
        x.strip().lower() for x in os.getenv("BLOCKED_MEDIA_TYPES", "").split(",") if x.strip()
    ]

    # ── Persistence ─────────────────────────────────────────────────────────
    def load_routes(self) -> list[Route]:
        ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not ROUTES_FILE.exists():
            return []
        with open(ROUTES_FILE) as f:
            data = json.load(f)
        return [Route(**r) for r in data]

    def save_routes(self, routes: list[Route]) -> None:
        ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ROUTES_FILE, "w") as f:
            json.dump([asdict(r) for r in routes], f, indent=2)

    def add_route(self, route: Route) -> None:
        routes = self.load_routes()
        routes.append(route)
        self.save_routes(routes)

    def remove_route(self, route_id: int) -> bool:
        routes = self.load_routes()
        new_routes = [r for r in routes if r.id != route_id]
        if len(new_routes) == len(routes):
            return False
        self.save_routes(new_routes)
        return True

    def next_route_id(self) -> int:
        routes = self.load_routes()
        return max((r.id for r in routes), default=0) + 1


settings = Settings()
