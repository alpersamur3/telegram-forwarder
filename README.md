# 📨 Telegram Forwarder Bot

A Pyrogram-based bot that forwards messages between channels, groups, and **forum topics**.  
Reads source chats without admin privileges (userbot mode).

---

## ✨ Features

| Feature | Details |
|---|---|
| **Userbot mode** | No admin access needed — just be a member of the chat |
| **Forum Topics** | Send to or read from specific topics via `message_thread_id` |
| **Copy mode** | Clean copy without the "Forwarded from" label |
| **Keyword filter** | Only forward messages containing specific keywords |
| **Rate limiter** | Sliding-window rate limiter to prevent bans |
| **FloodWait** | Catches Telegram's flood errors and waits automatically |
| **Media support** | Text, photos, videos, documents, audio, stickers, GIFs, polls, locations |
| **Edit sync** | Edits in the source are mirrored to the destination |
| **Delete sync** | Deletions in the source are mirrored to the destination |
| **Reply chains** | Replies are correctly linked in the destination chat |
| **Media groups** | Albums are forwarded intact using `send_media_group` |
| **Persistent routes** | Forwarding rules are saved to `data/routes.json` |

---

## 🚀 Setup

```bash
# 1. Clone the repo / download files
cd telegram-forwarder

# 2. Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the .env file
cp .env.example .env
nano .env   # Enter your API_ID and API_HASH
```

### API Credentials

1. Go to https://my.telegram.org/apps
2. Create an application → get your `api_id` and `api_hash`
3. Enter them in the `.env` file

---

## ⚙️ Mode Selection

### Userbot Mode (Recommended)
Leave `BOT_TOKEN=` **empty** in the `.env` file.  
On first run, you'll be prompted for your phone number + verification code.  
The session is saved to `forwarder_session.session`.

```bash
python main.py
# → Enter your phone number (+1234567890)
# → Enter the code sent by Telegram
```

### Bot Mode
Set `BOT_TOKEN=` to the token from @BotFather.  
Add the bot as a **member** to both source and destination chats.  
Note: Bots cannot read private/restricted channels.

---

## 📋 Commands

All commands are sent via **private message** unless stated otherwise.

### Adding Routes (Highly Flexible)
You can configure forwarding between channels, groups, and topics using the unified format: `CHAT_ID` or `CHAT_ID:TOPIC_ID`.

```
/add <source_id>[:source_topic_id] <destination_id>[:destination_topic_id] [filter_keywords ...]
```

#### Examples:
1. **Normal Forwarding (No Topics):**
   `/add -1001234567890 -1009876543210`
2. **Forwarding to a Specific Topic in Group:**
   `/add -1001234567890 -1009876543210:456`
3. **Forwarding from a Specific Topic in Group to a Channel:**
   `/add -1001234567890:123 -1009876543210`
4. **Topic-to-Topic Forwarding:**
   `/add -1001234567890:123 -1009876543210:456`
5. **Topic-to-Topic Forwarding with Keyword Filters:**
   `/add -1001234567890:123 -1009876543210:456 bitcoin ethereum`

> **Note:** To find a Topic ID, simply type `/chatid` inside that topic. The bot will automatically reply in your DM with both the Chat ID and the Topic ID.

### Other Commands
| Command | Alias | Description |
|---|---|---|
| `/list` | `/routes` | List all active forwarding rules |
| `/remove <id>` | `/del` | Remove a specific forwarding rule |
| `/status` | | Show bot statistics |
| `/topics <group_id>` | | List all topics and their IDs in a forum group chat |
| `/chatid` | | Show the current chat's ID (works in any chat, reply sent privately) |

---

## 🆔 Finding Chat IDs

**Method 1 — `/chatid` command (recommended):**  
Type `/chatid` in any group or channel. The bot will:
- Send the Chat ID (and Topic ID, if applicable) to you as a **private message**
- **Auto-delete** the `/chatid` command from the group to keep it clean

**Method 2 — Forward a channel message:**  
Forward any message from a **channel** to the bot via private message.  
The bot will automatically detect the source and show the Chat ID.  
> ⚠️ This only works for channel messages. For group messages, use `/chatid` instead — Telegram's API does not include the source group info when forwarding user messages.

**Method 3 — Web URL:**
- Right-click a message → "Copy link" → the number in the URL (prepend `-100`)

**Method 4 — Python script:**
```python
from pyrogram import Client
app = Client("my_account", api_id=..., api_hash=...)
async def main():
    async with app:
        async for dialog in app.get_dialogs():
            print(dialog.chat.title, dialog.chat.id)
import asyncio; asyncio.run(main())
```

---

## 🛡 Anti-Ban Best Practices

1. **Set rate limits**: Don't forward more than 20 messages per minute (default)
2. **Use FORWARD_DELAY**: 0.5s delay between each forward
3. **Copy mode**: `FORWARD_MODE=copy` — bypasses some restrictions
4. **Use a real account**: Avoid new or empty accounts
5. **Don't use VPNs**: Telegram flags suspicious IPs

---

## 📁 File Structure

```
telegram-forwarder/
├── main.py                  # Entry point
├── requirements.txt
├── .env.example             # Sample configuration
├── config/
│   └── settings.py          # Settings & route management
├── handlers/
│   └── forwarder.py         # Commands & message processing
├── utils/
│   ├── rate_limiter.py      # Anti-flood protection
│   ├── message_map.py       # Source→Dest message ID mapping
│   └── media_group_buffer.py # Album buffering & grouping
└── data/
    └── routes.json          # Persistent route list (auto-created)
```

---

## 🔧 Advanced Configuration

All configurable settings in the `.env` file:

```env
RATE_LIMIT_MESSAGES=20   # Max messages per time window
RATE_LIMIT_SECONDS=60    # Time window (seconds)
FORWARD_DELAY=0.5        # Delay between each forward (seconds)
FORWARD_MODE=copy        # "copy" or "forward"
ADMIN_IDS=123,456        # User IDs allowed to manage the bot

# Whitelist: Comma-separated list of allowed content types (empty = all allowed)
# Options: text, photo, video, document, audio, voice, video_note, sticker, animation, poll, location, contact, venue, game
ALLOWED_MEDIA_TYPES=text,photo,video

# Blacklist: Comma-separated list of blocked content types (empty = none blocked)
BLOCKED_MEDIA_TYPES=sticker,poll
```

---

## 📜 License

MIT — Free for personal and commercial use.
