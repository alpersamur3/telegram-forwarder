"""
Telegram Message Forwarder Bot — v2
Features: edit sync, delete sync, media group support, topic routing
"""

import asyncio
import logging
from pyrogram import Client, enums, filters, idle
from pyrogram.types import Message
from pyrogram.errors import PeerIdInvalid, UserIsBlocked
import pyrogram.utils

# ── Monkey-patch: Pyrogram 2.0.106 does not support newer large channel IDs ──
# Telegram's newer channel IDs exceed 2^31, which get_peer_type doesn't handle.
_CHANNEL_THRESHOLD = -1_000_000_000_000

_orig_get_peer_type = pyrogram.utils.get_peer_type


def _patched_get_peer_type(peer_id: int) -> str:
    if peer_id < 0:
        if peer_id >= _CHANNEL_THRESHOLD:
            return "chat"
        return "channel"
    elif peer_id > 0:
        return "user"
    raise ValueError(f"Peer id invalid: {peer_id}")


pyrogram.utils.get_peer_type = _patched_get_peer_type
# ─────────────────────────────────────────────────────────────────────────────

from config.settings import settings
from handlers.forwarder import ForwarderHandler
from utils.rate_limiter import RateLimiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("🚀 Starting Telegram Forwarder Bot v2...")

    app = Client(
        name=settings.SESSION_NAME,
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        bot_token=settings.BOT_TOKEN if settings.BOT_TOKEN else None,
    )

    rate_limiter = RateLimiter(
        max_messages=settings.RATE_LIMIT_MESSAGES,
        per_seconds=settings.RATE_LIMIT_SECONDS,
    )
    forwarder = ForwarderHandler(app, rate_limiter, settings)

    # ── Commands (private messages only) ─────────────────────────────────────

    @app.on_message(filters.command("start") & filters.private)
    async def cmd_start(client: Client, message: Message):
        await message.reply_text(
            "👋 **Telegram Forwarder Bot v2**\n\n"
            "**Commands:**\n"
            "`/add <src> <dest>` — Add a forwarding rule (e.g. `/add -1001:5 -1002:10`)\n"
            "`/list` — List active routes (alias: `/routes`)\n"
            "`/remove <id>` — Remove a route (alias: `/del`)\n"
            "`/status` — Bot statistics\n"
            "`/topics <group_id>` — List all topics in a forum group\n"
            "`/chatid` — Get the current chat's ID (replies privately, cleans up trigger)\n\n"
            "**💡 Finding Chat IDs:**\n"
            "• **Channels:** Forward any message from a channel to this chat — "
            "the Chat ID will be shown automatically.\n"
            "• **Groups:** Type `/chatid` in the group — the ID will be sent "
            "to you privately and the command will be auto-deleted.\n\n"
            "**Supported features:**\n"
            "📦 Media groups (albums)\n"
            "💬 Reply chain preservation\n"
            "✏️ Edit synchronization\n"
            "🗑 Delete synchronization\n"
            "🎨 Text formatting (bold/italic/links)",
        )

    @app.on_message(filters.command("add") & filters.private)
    async def cmd_add_route(client: Client, message: Message):
        await forwarder.cmd_add_route(message)

    @app.on_message(filters.command(["list", "routes"]) & filters.private)
    async def cmd_list_routes(client: Client, message: Message):
        await forwarder.cmd_list_routes(message)

    @app.on_message(filters.command(["remove", "del"]) & filters.private)
    async def cmd_remove_route(client: Client, message: Message):
        await forwarder.cmd_remove_route(message)

    @app.on_message(filters.command("status") & filters.private)
    async def cmd_status(client: Client, message: Message):
        await forwarder.cmd_status(message)

    @app.on_message(filters.command("topics") & filters.private)
    async def cmd_list_topics(client: Client, message: Message):
        await forwarder.cmd_list_topics(message)

    # ── /chatid — replies via DM, auto-deletes from group ────────────────────

    @app.on_message(filters.command("chatid"))
    async def cmd_chatid(client: Client, message: Message):
        """Show the current chat's ID. Replies privately and cleans up."""
        user_id = message.from_user.id if message.from_user else None
        if settings.ADMIN_IDS and user_id and user_id not in settings.ADMIN_IDS:
            return  # Ignore completely if ADMIN_IDS is set and the user is not in it

        chat = message.chat
        chat_type_map = {
            "private": "👤 Private",
            "group": "👥 Group",
            "supergroup": "👥 Supergroup",
            "channel": "📢 Channel",
        }
        chat_type = chat_type_map.get(chat.type.value, chat.type.value)
        title = chat.title or chat.first_name or "(no title)"

        lines = [
            f"🆔 **Chat ID Info**\n",
            f"{chat_type}: **{title}**",
            f"Chat ID: `{chat.id}`",
        ]

        topic_id = getattr(message, "reply_to_top_message_id", None) or getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
        if topic_id:
            lines.append(f"Topic ID: `{topic_id}`")

        if chat.type.value != "private":
            lines.append(f"\n💡 To add a route:")
            lines.append(f"`/add_route {chat.id} <dest_id>`")

        text = "\n".join(lines)

        if chat.type.value == "private":
            # In private chat, just reply normally
            await message.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)
        else:
            # In groups: send the info as a private message to the user
            user_id = message.from_user.id if message.from_user else None
            if user_id:
                try:
                    await client.send_message(
                        user_id, text, parse_mode=enums.ParseMode.MARKDOWN
                    )
                except (PeerIdInvalid, UserIsBlocked):
                    # Can't DM the user — send in group as fallback
                    await message.reply_text(text, parse_mode=enums.ParseMode.MARKDOWN)
                    return

            # Auto-delete the /chatid command from the group
            try:
                await message.delete()
            except Exception:
                pass  # May lack delete permissions

    # ── Auto-detect forwarded messages (channel ID lookup) ───────────────────

    @app.on_message(filters.forwarded & filters.private)
    async def on_forwarded_private(client: Client, message: Message):
        """Automatically show Chat ID when a message is forwarded to the bot."""
        fwd = message.forward_from_chat
        if fwd:
            chat_type = "📢 Channel" if fwd.type.value == "channel" else "👥 Group"
            title = fwd.title or "(no title)"
            lines = [
                f"🆔 **Chat ID Info**\n",
                f"{chat_type}: **{title}**",
                f"Chat ID: `{fwd.id}`",
            ]
            topic_id = getattr(message, "forward_from_message_id", None)
            thread_id = getattr(message, "reply_to_top_message_id", None) or getattr(message, "message_thread_id", None) or getattr(message, "reply_to_message_id", None)
            if thread_id:
                lines.append(f"Topic ID: `{thread_id}`")
            lines.append(f"\n💡 To add a route:")
            lines.append(f"`/add_route {fwd.id} <dest_id>`")
            await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)
        elif message.forward_from:
            user = message.forward_from
            name = user.first_name or ""
            if user.last_name:
                name += f" {user.last_name}"
            await message.reply_text(
                f"🆔 **User ID Info**\n\n"
                f"👤 User: **{name}**\n"
                f"User ID: `{user.id}`\n"
                f"Username: @{user.username or '(none)'}\n\n"
                f"⚠️ If this message was forwarded from a group, the group's "
                f"Chat ID is not included. Use `/chatid` inside the group instead.",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        else:
            await message.reply_text(
                "⚠️ The source info of this message is hidden.\n"
                "The sender's privacy settings prevent forwarding metadata.\n\n"
                "💡 To find a group's ID, type `/chatid` inside that group.",
            )

    # ── Message events (groups & channels) ───────────────────────────────────

    @app.on_message(~filters.private & ~filters.command("chatid"))
    async def on_message(client: Client, message: Message):
        """New message — forward it."""
        await forwarder.process_message(message)

    @app.on_edited_message(~filters.private)
    async def on_edit(client: Client, message: Message):
        """Edited message — sync the edit to destination."""
        await forwarder.process_edit(message)

    @app.on_deleted_messages()
    async def on_delete(client: Client, messages):
        """
        Deleted messages — Pyrogram fires this for channel/group deletions.
        Chat info may not always be available (channel restriction).
        """
        if not messages:
            return
        msg_list = messages if isinstance(messages, list) else [messages]
        for msg in msg_list:
            chat_id = getattr(msg.chat, "id", None) if msg.chat else None
            await forwarder.process_delete(chat_id, [msg.id])

    # ── Start ────────────────────────────────────────────────────────────────

    async with app:
        me = await app.get_me()
        logger.info(f"✅ Logged in: {me.first_name} (@{me.username}) | ID: {me.id}")
        logger.info(f"📋 Active routes: {len(settings.load_routes())}")
        await idle()


if __name__ == "__main__":
    asyncio.run(main())
