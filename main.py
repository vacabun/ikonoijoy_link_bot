import logging
import sys
from pathlib import Path

from src.auth.manager import AuthManager
from src.bot.forwarder import EqualLoveForwardBot
from src.config import load_settings
from src.storage.state import StateManager
from src.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)


def _setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(data_dir / "bot.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    settings = load_settings(config_path)
    runtime = settings["runtime"]
    telegram = settings["telegram"]

    data_dir = Path(runtime["data_dir"])
    _setup_logging(data_dir)

    sender = TelegramSender(
        bot_token=telegram["bot_token"],
        default_chat_id=telegram.get("chat_id"),
        system_chat_id=telegram.get("system_chat_id"),
        room_chat_ids=telegram.get("room_chat_ids"),
    )
    auth_managers = [
        AuthManager(
            auth_config=account,
            cache_path=account["cache_path"],
            name=account["name"],
        )
        for account in settings["equal_love_accounts"]
    ]
    state = StateManager(
        db_path=runtime["state_db_path"],
        initial_cursor=0 if runtime["forward_history_on_first_run"] else None,
    )
    bot = EqualLoveForwardBot(
        auth_managers=auth_managers,
        sender=sender,
        state=state,
        poll_interval=int(runtime["poll_interval_seconds"]),
        page_size=int(runtime["page_size"]),
        max_pages_per_room=int(runtime["max_pages_per_room"]),
        startup_backfill_hours=int(runtime["startup_backfill_hours"]),
        startup_fallback_count=int(runtime["startup_fallback_count"]),
    )

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt.")
        sender.send_system_notification("Bot stopped.")
    finally:
        state.close()


if __name__ == "__main__":
    main()
