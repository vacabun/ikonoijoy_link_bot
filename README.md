# Equal Love Telegram Forward Bot

Polls `equal-love.link` talk rooms and forwards new messages to Telegram.

## Features

- Automatic login and access token refresh
- Periodic polling for subscribed and accessible talk rooms
- Duplicate prevention with SQLite state
- Text, image, and video forwarding
- Optional per-room Telegram routing by room name or room ID
- Startup replay: sends the latest messages on every start
- Normal polling checks every accessible room by cursor, not by unread count

## Project Structure

- `main.py`: root entry point; loads `config.json` and starts the bot
- `src/bot/forwarder.py`: polling and forwarding workflow
- `src/telegram/sender.py`: Telegram Bot API sender
- `src/storage/state.py`: SQLite state management
- `src/auth/credentials.py`: login and token refresh helpers
- `src/auth/manager.py`: auth lifecycle and client rebuilds
- `src/clients/equal_love.py`: equal-love.link API client
- `src/config/settings.py`: JSON config loader and validator

## Configuration

All configuration is stored in `config.json`. `.env` is no longer used.

Copy the template:

```bash
cp config.template.json config.json
```

Example:

```json
{
  "telegram": {
    "bot_token": "<your_telegram_bot_token>",
    "chat_id": "<default_chat_id>",
    "system_chat_id": "",
    "room_chat_ids": {
      "大谷 映美里": "-1001234567890",
      "1": "-1001234567891"
    }
  },
  "equal_love": {
    "username": "<login username or email>",
    "password": "<login password>",
    "x_request_verification_key": "<required request verification key>",
    "x_artist_group_uuid": "<required artist group UUID>"
  },
  "runtime": {
    "data_dir": "data",
    "auth_cache_path": "data/auth_cache.json",
    "state_db_path": "data/state.db",
    "poll_interval_seconds": 300,
    "page_size": 50,
    "max_pages_per_room": 5,
    "startup_replay_count": 2,
    "forward_history_on_first_run": false
  }
}
```

### Telegram

- `telegram.bot_token`: Telegram bot token.
- `telegram.chat_id`: Default destination chat/channel ID.
- `telegram.system_chat_id`: Optional chat/channel for startup and shutdown notifications. Falls back to `chat_id` if empty.
- `telegram.room_chat_ids`: Optional per-room routing map. Keys can be talk room names or talk room ID strings.

If a room does not match `room_chat_ids`, messages are sent to `telegram.chat_id`.

### Equal Love

- `equal_love.username`: Login username or email.
- `equal_love.password`: Login password.
- `equal_love.x_request_verification_key`: Request verification key from the app traffic.
- `equal_love.x_artist_group_uuid`: Artist group UUID from the app traffic.

Tokens are stored in `runtime.auth_cache_path`. If the access token expires, the bot refreshes it automatically. If refresh fails, it logs in again with username/password.

### Runtime

- `runtime.poll_interval_seconds`: Seconds between polling cycles.
- `runtime.page_size`: Chat page size for API requests.
- `runtime.max_pages_per_room`: Maximum pages fetched per room in one cycle.
- `runtime.startup_replay_count`: Number of latest messages to send for each accessible room on startup.
- `runtime.forward_history_on_first_run`: If `false`, first run starts from the current time and does not backfill old messages.

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start with the default config:

```bash
python main.py
```

Use a custom config path:

```bash
python main.py path/to/config.json
```

## Forwarded Message Format

Text message:

```text
#大谷映美里 2026/04/21 18:36:07

水光カラコンも浴衣も解禁になったよ！🗝️
```

Media messages:

- Multiple photos/videos from the same message are sent as one Telegram media group when possible.
- The first media item in the group includes the caption.
- Unsupported media group items fall back to individual sends.

## Notes

- Add the bot to your Telegram channel/chat before running.
- Give the bot permission to send messages.
- Use a test channel first to verify routing.
- Keep `config.json` and `data/auth_cache.json` private.
