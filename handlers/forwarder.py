"""
ForwarderHandler — message forwarding engine.

Features:
  ✅ Media groups (albums) — full support via send_media_group
  ✅ Reply chains — replies are correctly linked in destination
  ✅ Edit sync — edits in source are mirrored to destination
  ✅ Delete sync — deletions in source are mirrored to destination
  ✅ Text formatting — entities are preserved (bold/italic/links)
"""

import asyncio
import logging
from typing import Optional

from pyrogram import Client, enums, raw
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    InputMediaAnimation,
)
from pyrogram.errors import (
    FloodWait,
    ChatForwardsRestricted,
    MessageIdInvalid,
    MessageNotModified,
    ChatAdminRequired,
)

from config.settings import Settings, Route
from utils.rate_limiter import RateLimiter
from utils.message_map import MessageMap
from utils.media_group_buffer import MediaGroupBuffer

logger = logging.getLogger(__name__)


class ForwarderHandler:
    def __init__(self, client: Client, rate_limiter: RateLimiter, settings: Settings):
        self.client = client
        self.rl = rate_limiter
        self.settings = settings
        self._forwarded_count = 0
        self._error_count = 0

        # Message ID mapping table (for reply/edit/delete sync)
        self.msg_map = MessageMap()

        # Media group buffer
        self.mg_buffer = MediaGroupBuffer(flush_delay=0.8)
        self.mg_buffer.set_callback(self._flush_media_group)

    # ─────────────────────────────────────────────────────────────────────────
    # Command handlers
    # ─────────────────────────────────────────────────────────────────────────

    async def cmd_add_route(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        parts = message.text.split()
        if len(parts) < 3:
            await message.reply(
                "Usage: `/add <source_id>[:topic_id] <dest_id>[:topic_id] [filter ...]`\n\n"
                "Examples:\n"
                "• Normal: `/add -100123 -100456`\n"
                "• To Topic: `/add -100123 -100456:12`\n"
                "• From Topic: `/add -100123:5 -100456`\n"
                "• Topic to Topic: `/add -100123:5 -100456:12`",
                parse_mode=enums.ParseMode.MARKDOWN,
            )
            return
        try:
            def parse_target(arg: str) -> tuple[int, Optional[int]]:
                if ":" in arg:
                    c_str, t_str = arg.split(":", 1)
                    return int(c_str), int(t_str)
                return int(arg), None

            source_chat, source_topic = parse_target(parts[1])
            dest_chat, dest_topic = parse_target(parts[2])
            keywords = parts[3:]
        except ValueError:
            await message.reply("❌ Invalid format. Please use integers for IDs and Topic IDs.")
            return

        route = Route(
            id=self.settings.next_route_id(),
            source_chat_id=source_chat,
            source_topic_id=source_topic,
            dest_chat_id=dest_chat,
            dest_topic_id=dest_topic,
            filters_text=keywords,
        )
        self.settings.add_route(route)

        src_disp = f"`{source_chat}`" + (f" › Topic `{source_topic}`" if source_topic else "")
        dst_disp = f"`{dest_chat}`" + (f" › Topic `{dest_topic}`" if dest_topic else "")
        await message.reply(
            f"✅ Route added (ID: `{route.id}`)\n"
            f"📥 Source: {src_disp}\n📤 Destination: {dst_disp}\n"
            f"🔑 Filters: {keywords or 'none'}",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    async def cmd_list_routes(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        routes = self.settings.load_routes()
        if not routes:
            await message.reply("📭 No routes configured yet.")
            return
        lines = ["**📋 Active Routes:**\n"]
        for r in routes:
            st = f" › T{r.source_topic_id}" if r.source_topic_id else ""
            dt = f" › T{r.dest_topic_id}" if r.dest_topic_id else ""
            em = "✅" if r.enabled else "⏸"
            lines.append(f"{em} `#{r.id}` | `{r.source_chat_id}{st}` → `{r.dest_chat_id}{dt}`")
            if r.filters_text:
                lines.append(f"   🔑 {', '.join(r.filters_text)}")
        await message.reply("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)

    async def cmd_remove_route(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply("Usage: `/remove <id>` (or `/del <id>`)", parse_mode=enums.ParseMode.MARKDOWN)
            return
        try:
            rid = int(parts[1])
        except ValueError:
            await message.reply("❌ Invalid ID.")
            return
        if self.settings.remove_route(rid):
            await message.reply(f"🗑 Route `#{rid}` removed.", parse_mode=enums.ParseMode.MARKDOWN)
        else:
            await message.reply(f"❌ Route `#{rid}` not found.", parse_mode=enums.ParseMode.MARKDOWN)

    async def cmd_status(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        routes = self.settings.load_routes()
        await message.reply(
            f"**📊 Bot Status**\n\n"
            f"🔀 Active routes: `{len(routes)}`\n"
            f"✉️ Total forwarded: `{self._forwarded_count}`\n"
            f"❌ Errors: `{self._error_count}`\n"
            f"🗂 Message map: `{len(self.msg_map)}` entries\n"
            f"⚡ Mode: `{self.settings.FORWARD_MODE}`\n"
            f"🛡 Rate limit: `{self.settings.RATE_LIMIT_MESSAGES}` msg / "
            f"`{self.settings.RATE_LIMIT_SECONDS}` sec",
            parse_mode=enums.ParseMode.MARKDOWN,
        )

    async def cmd_list_topics(self, message: Message) -> None:
        if not self._is_admin(message.from_user.id):
            await message.reply("⛔ Unauthorized.")
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply("Usage: `/topics <group_id>`", parse_mode=enums.ParseMode.MARKDOWN)
            return

        try:
            chat_id = int(parts[1])
        except ValueError:
            await message.reply("❌ Invalid group ID. Must be an integer starting with -100.")
            return

        try:
            peer = await self.client.resolve_peer(chat_id)
            r = await self.client.invoke(
                raw.functions.channels.GetForumTopics(
                    channel=peer,
                    offset_date=0,
                    offset_id=0,
                    offset_topic=0,
                    limit=100
                )
            )

            if not r.topics:
                await message.reply("📭 No topics found in this group (or forum topics are not enabled).")
                return

            lines = [f"💬 **Topics in Group `{chat_id}`:**\n"]
            for t in r.topics:
                if isinstance(t, raw.types.ForumTopic):
                    lines.append(f"• **{t.title}** | Topic ID: `{t.id}`")

            await message.reply("\n".join(lines), parse_mode=enums.ParseMode.MARKDOWN)

        except Exception as e:
            await message.reply(f"❌ Failed to fetch topics: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Incoming message processing
    # ─────────────────────────────────────────────────────────────────────────

    async def process_message(self, message: Message) -> None:
        """Called for every incoming message."""
        if not self._is_allowed_message(message):
            return

        # If part of a media group, buffer it — the buffer handles flushing
        if message.media_group_id:
            await self.mg_buffer.add(message)
            return

        chat_id = message.chat.id
        topic_id: Optional[int] = getattr(message, "message_thread_id", None)
        routes = self._matched_routes(chat_id, topic_id, message)

        for route in routes:
            await self._dispatch(message, route)
            await asyncio.sleep(self.settings.FORWARD_DELAY)

    async def process_edit(self, message: Message) -> None:
        """Sync edited messages to destination."""
        matches = self.msg_map.get_all_routes(message.chat.id, message.id)
        if not matches:
            return

        for route_id, dst_chat, dst_msg in matches:
            try:
                if message.text is not None:
                    await self.client.edit_message_text(
                        chat_id=dst_chat,
                        message_id=dst_msg,
                        text=message.text,
                        entities=message.entities,
                        disable_web_page_preview=True,
                    )
                elif message.caption is not None:
                    await self.client.edit_message_caption(
                        chat_id=dst_chat,
                        message_id=dst_msg,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                    )
                logger.info(f"✏️ Edit sync: {message.chat.id}/{message.id} → {dst_chat}/{dst_msg}")
            except MessageNotModified:
                pass
            except Exception as e:
                logger.warning(f"⚠️ Edit sync error ({dst_chat}/{dst_msg}): {e}")

    async def process_delete(self, chat_id: Optional[int], message_ids: list[int]) -> None:
        """Sync deleted messages to destination."""
        for src_msg_id in message_ids:
            if chat_id is not None:
                matches = [(chat_id, rid, dc, dm) for rid, dc, dm in self.msg_map.get_all_routes(chat_id, src_msg_id)]
            else:
                matches = self.msg_map.get_all_routes_by_msg_id(src_msg_id)

            for sc, route_id, dst_chat, dst_msg in matches:
                try:
                    await self.client.delete_messages(dst_chat, dst_msg)
                    self.msg_map.delete(sc, src_msg_id, route_id)
                    logger.info(f"🗑 Delete sync: {sc}/{src_msg_id} → {dst_chat}/{dst_msg}")
                except Exception as e:
                    logger.warning(f"⚠️ Delete sync error ({dst_chat}/{dst_msg}): {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Media group (album) sending
    # ─────────────────────────────────────────────────────────────────────────

    async def _flush_media_group(self, messages: list[Message]) -> None:
        """
        Called by the buffer. Receives all messages from a single album
        once the buffer flush delay expires.
        """
        if not messages:
            return

        # All messages are guaranteed to be from the same chat/topic
        first = messages[0]
        chat_id = first.chat.id
        topic_id: Optional[int] = getattr(first, "message_thread_id", None)
        routes = self._matched_routes(chat_id, topic_id, first)

        for route in routes:
            await self._send_media_group(messages, route)
            await asyncio.sleep(self.settings.FORWARD_DELAY)

    async def _send_media_group(self, messages: list[Message], route: Route) -> None:
        """Send a complete album via send_media_group."""
        await self.rl.acquire()

        media_list = []
        # First message's caption is used as the album caption
        for i, msg in enumerate(messages):
            caption = msg.caption if i == 0 else None
            caption_entities = msg.caption_entities if i == 0 else None

            if msg.photo:
                media_list.append(InputMediaPhoto(
                    media=msg.photo.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                ))
            elif msg.video:
                media_list.append(InputMediaVideo(
                    media=msg.video.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                ))
            elif msg.document:
                media_list.append(InputMediaDocument(
                    media=msg.document.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                ))
            elif msg.audio:
                media_list.append(InputMediaAudio(
                    media=msg.audio.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                ))
            elif msg.animation:
                media_list.append(InputMediaAnimation(
                    media=msg.animation.file_id,
                    caption=caption,
                    caption_entities=caption_entities,
                ))

        if not media_list:
            return

        # Reply chain or topic targeting
        reply_to = self._resolve_reply(messages[0], route)

        try:
            sent = await self.client.send_media_group(
                chat_id=route.dest_chat_id,
                media=media_list,
                reply_to_message_id=reply_to,
            )
            # Update mapping table (for edit/delete sync)
            src_ids = [m.id for m in messages]
            dst_ids = [m.id for m in sent]
            self.msg_map.put_many(
                messages[0].chat.id, src_ids,
                route.id,
                route.dest_chat_id, dst_ids,
            )
            self._forwarded_count += len(sent)
            logger.info(
                f"📦 Album forwarded ({len(sent)} media) | "
                f"{messages[0].chat.id} → {route.dest_chat_id}"
            )
        except FloodWait as e:
            logger.warning(f"⏳ FloodWait {e.value}s")
            await asyncio.sleep(e.value + 1)
            await self._send_media_group(messages, route)
        except Exception as e:
            logger.error(f"❌ Album send error: {e}", exc_info=True)
            self._error_count += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Single message forwarding
    # ─────────────────────────────────────────────────────────────────────────

    async def _dispatch(self, message: Message, route: Route) -> None:
        """Forward a single message (copy or native forward mode)."""
        await self.rl.acquire()
        try:
            if self.settings.FORWARD_MODE == "forward":
                sent_list = await self.client.forward_messages(
                    chat_id=route.dest_chat_id,
                    from_chat_id=message.chat.id,
                    message_ids=message.id,
                )
                sent = sent_list[0] if isinstance(sent_list, list) else sent_list
            else:
                sent = await self._copy_message(message, route)

            if sent:
                self.msg_map.put(
                    message.chat.id, message.id, route.id,
                    route.dest_chat_id, sent.id,
                )
            self._forwarded_count += 1
            logger.info(f"✅ Forwarded {message.id} | {message.chat.id} → {route.dest_chat_id}")

        except FloodWait as e:
            logger.warning(f"⏳ FloodWait {e.value}s")
            await asyncio.sleep(e.value + 1)
            await self._dispatch(message, route)

        except ChatForwardsRestricted:
            logger.warning("⚠️ Chat forwards restricted, falling back to copy mode")
            try:
                sent = await self._copy_message(message, route)
                if sent:
                    self.msg_map.put(
                        message.chat.id, message.id, route.id,
                        route.dest_chat_id, sent.id,
                    )
            except Exception as e2:
                logger.error(f"❌ Fallback copy error: {e2}")
                self._error_count += 1

        except MessageIdInvalid:
            logger.warning(f"⚠️ Message {message.id} no longer exists.")
            self._error_count += 1

        except Exception as exc:
            logger.error(f"❌ Dispatch error: {exc}", exc_info=True)
            self._error_count += 1

    async def _copy_message(self, message: Message, route: Route) -> Optional[Message]:
        """
        Send a clean copy of the message preserving formatting (entities).
        Returns: the sent Message object (needed for reply/edit sync).
        """
        dest = route.dest_chat_id
        reply_to = self._resolve_reply(message, route)

        # ── Detect media type via enum (more reliable than attribute checks) ──
        media = message.media

        # Poll, location, contact — use copy_message (safest approach)
        if media in (
            enums.MessageMediaType.POLL,
            enums.MessageMediaType.LOCATION,
            enums.MessageMediaType.CONTACT,
            enums.MessageMediaType.VENUE,
            enums.MessageMediaType.DICE,
            enums.MessageMediaType.GAME,
        ):
            return await self.client.copy_message(
                chat_id=dest,
                from_chat_id=message.chat.id,
                message_id=message.id,
                reply_to_message_id=reply_to,
            )

        # ── Media files — send with preserved entities ────────────────────────
        if message.photo:
            return await self.client.send_photo(
                dest, message.photo.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )
        elif message.video:
            return await self.client.send_video(
                dest, message.video.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )
        elif message.document:
            return await self.client.send_document(
                dest, message.document.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )
        elif message.audio:
            return await self.client.send_audio(
                dest, message.audio.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )
        elif message.voice:
            return await self.client.send_voice(
                dest, message.voice.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )
        elif message.video_note:
            return await self.client.send_video_note(
                dest, message.video_note.file_id,
                reply_to_message_id=reply_to,
            )
        elif message.sticker:
            return await self.client.send_sticker(
                dest, message.sticker.file_id,
                reply_to_message_id=reply_to,
            )
        elif message.animation:
            return await self.client.send_animation(
                dest, message.animation.file_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
                reply_to_message_id=reply_to,
            )

        # ── Plain text ────────────────────────────────────────────────────────
        elif message.text:
            return await self.client.send_message(
                dest, message.text,
                entities=message.entities,  # preserve formatting
                reply_to_message_id=reply_to,
                disable_web_page_preview=not bool(message.web_page),
            )

        # ── Fallback: use copy_message for anything else ─────────────────────
        else:
            logger.info(f"🔍 FALLBACK copy_message for message {message.id}")
            try:
                return await self.client.copy_message(
                    chat_id=dest,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                    reply_to_message_id=reply_to,
                )
            except Exception as e:
                logger.warning(f"⚠️ copy_message failed ({e}). Trying forward_messages as fallback...")
                try:
                    if route.dest_topic_id:
                        logger.info(f"🔍 FALLBACK forward topic raw invoke for msg {message.id}")
                        r = await self.client.invoke(
                            raw.functions.messages.ForwardMessages(
                                to_peer=await self.client.resolve_peer(dest),
                                from_peer=await self.client.resolve_peer(message.chat.id),
                                id=[message.id],
                                random_id=[self.client.rnd_id()],
                                top_msg_id=route.dest_topic_id
                            )
                        )
                        forwarded_messages = []
                        users = {i.id: i for i in r.users}
                        chats = {i.id: i for i in r.chats}
                        for i in r.updates:
                            if isinstance(i, (raw.types.UpdateNewMessage,
                                              raw.types.UpdateNewChannelMessage,
                                              raw.types.UpdateNewScheduledMessage)):
                                forwarded_messages.append(
                                    await Message._parse(
                                        self.client, i.message,
                                        users, chats
                                    )
                                )
                        sent = forwarded_messages[0] if forwarded_messages else None
                    else:
                        logger.info(f"🔍 FALLBACK forward generic for msg {message.id}")
                        sent_list = await self.client.forward_messages(
                            chat_id=dest,
                            from_chat_id=message.chat.id,
                            message_ids=message.id,
                        )
                        sent = sent_list[0] if isinstance(sent_list, list) else sent_list
                    return sent
                except Exception as fe:
                    logger.error(f"❌ Fallback forward_messages also failed: {fe}")
                    return None

    # ─────────────────────────────────────────────────────────────────────────
    # Helper methods
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_reply(self, message: Message, route: Route) -> Optional[int]:
        """
        Determine reply_to_message_id for the destination message.

        Priority:
          1. If the source message replies to another message, find the
             corresponding destination message ID (reply chain preservation).
          2. Otherwise, if the route targets a specific topic, return the
             topic ID so the message lands in the correct forum topic.
             (Pyrogram 2.0.106 uses reply_to_message_id for topic targeting.)
          3. None if neither applies.
        """
        if message.reply_to_message_id:
            mapping = self.msg_map.get(message.chat.id, message.reply_to_message_id, route.id)
            if mapping:
                _, dst_msg_id = mapping
                return dst_msg_id
        # Fall back to topic targeting
        return route.dest_topic_id

    def _matched_routes(
        self,
        chat_id: int,
        topic_id: Optional[int],
        message: Message,
    ) -> list[Route]:
        routes = self.settings.load_routes()
        matched = []
        for r in routes:
            if not r.enabled or not r.matches(chat_id, topic_id):
                continue
            if r.filters_text:
                text = (message.text or message.caption or "").lower()
                if not any(kw.lower() in text for kw in r.filters_text):
                    continue
            matched.append(r)
        return matched

    def _is_admin(self, user_id: int) -> bool:
        if not self.settings.ADMIN_IDS:
            return True
        return user_id in self.settings.ADMIN_IDS

    def _get_message_content_type(self, message: Message) -> str:
        if message.photo:
            return "photo"
        if message.video:
            return "video"
        if message.document:
            return "document"
        if message.audio:
            return "audio"
        if message.voice:
            return "voice"
        if message.video_note:
            return "video_note"
        if message.sticker:
            return "sticker"
        if message.animation:
            return "animation"
        if message.poll or message.media == enums.MessageMediaType.POLL:
            return "poll"
        if message.location or message.media == enums.MessageMediaType.LOCATION:
            return "location"
        if message.contact or message.media == enums.MessageMediaType.CONTACT:
            return "contact"
        if message.venue or message.media == enums.MessageMediaType.VENUE:
            return "venue"
        if message.game or message.media == enums.MessageMediaType.GAME:
            return "game"
        if message.text:
            return "text"
        return "other"

    def _is_allowed_message(self, message: Message) -> bool:
        content_type = self._get_message_content_type(message)
        
        # Whitelist check
        if self.settings.ALLOWED_MEDIA_TYPES:
            if content_type not in self.settings.ALLOWED_MEDIA_TYPES:
                logger.info(
                    f"🚫 Skipping message {message.id}: type '{content_type}' is not in the ALLOWED_MEDIA_TYPES whitelist."
                )
                return False
                
        # Blacklist check
        if self.settings.BLOCKED_MEDIA_TYPES:
            if content_type in self.settings.BLOCKED_MEDIA_TYPES:
                logger.info(
                    f"🚫 Skipping message {message.id}: type '{content_type}' is in the BLOCKED_MEDIA_TYPES blacklist."
                )
                return False
                
        return True
