import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from src.auth.manager import AuthManager
from src.bot.forwarder import EqualLoveForwardBot
from src.config import load_settings
from src.storage.state import StateManager
from src.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)
_LOGGING_LOCK = threading.Lock()
_LOGGING_CONFIGURED = False
_LOG_FILE_PATHS: set[Path] = set()


@dataclass
class BotRuntime:
    config_path: Path
    bot: EqualLoveForwardBot
    sender: TelegramSender
    state: StateManager


def _setup_logging(data_dir: Path) -> None:
    global _LOGGING_CONFIGURED

    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = (data_dir / "bot.log").resolve()
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with _LOGGING_LOCK:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        if not _LOGGING_CONFIGURED:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            root_logger.addHandler(stream_handler)
            _LOGGING_CONFIGURED = True

        if log_path not in _LOG_FILE_PATHS:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            _LOG_FILE_PATHS.add(log_path)


def _config_paths(path_arg: str) -> list[Path]:
    path = Path(path_arg)
    if path.is_dir():
        paths = [
            item
            for item in sorted(path.glob("*.json"))
            if not item.name.startswith(".") and not item.name.endswith(".template.json")
        ]
        if not paths:
            raise ValueError(f"No JSON config files found in directory: {path}")
        return paths
    return [path]


def _build_runtime(config_path: Path) -> BotRuntime:
    settings = load_settings(config_path)
    runtime = settings["runtime"]
    telegram = settings["telegram"]

    data_dir = Path(runtime["data_dir"])
    _setup_logging(data_dir)
    logger.info("Loading config: %s", config_path)

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
    stop_event = threading.Event()
    bot = EqualLoveForwardBot(
        auth_managers=auth_managers,
        sender=sender,
        state=state,
        poll_interval=int(runtime["poll_interval_seconds"]),
        page_size=int(runtime["page_size"]),
        max_pages_per_room=int(runtime["max_pages_per_room"]),
        startup_backfill_hours=int(runtime["startup_backfill_hours"]),
        startup_fallback_count=int(runtime["startup_fallback_count"]),
        stop_event=stop_event,
    )
    return BotRuntime(
        config_path=config_path,
        bot=bot,
        sender=sender,
        state=state,
    )


def _run_runtime(runtime: BotRuntime) -> None:
    try:
        runtime.bot.run()
    except Exception:
        logger.exception("Bot crashed for config: %s", runtime.config_path)
        runtime.sender.send_system_notification(f"Bot crashed: {runtime.config_path}")


def main() -> None:
    config_arg = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    runtimes = [_build_runtime(config_path) for config_path in _config_paths(config_arg)]
    threads = [
        threading.Thread(
            target=_run_runtime,
            args=(runtime,),
            name=f"bot:{runtime.config_path.name}",
            daemon=True,
        )
        for runtime in runtimes
    ]

    for thread in threads:
        thread.start()

    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt.")
        for runtime in runtimes:
            runtime.bot.stop()
            runtime.sender.send_system_notification(f"Bot stopped: {runtime.config_path}")
        for thread in threads:
            thread.join(timeout=10)
    finally:
        for runtime in runtimes:
            runtime.state.close()


if __name__ == "__main__":
    main()
