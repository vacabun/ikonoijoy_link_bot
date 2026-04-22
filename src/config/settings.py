import json
from pathlib import Path
from typing import Any


def load_settings(config_path: str = "config.json") -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as file:
        settings = json.load(file)

    if not isinstance(settings, dict):
        raise ValueError("config.json must be a JSON object.")

    settings.setdefault("runtime", {})
    settings.setdefault("telegram", {})
    settings.setdefault("equal_love", {})

    runtime = settings["runtime"]
    telegram = settings["telegram"]
    equal_love = settings["equal_love"]

    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a JSON object.")
    if not isinstance(telegram, dict):
        raise ValueError("telegram must be a JSON object.")
    if not isinstance(equal_love, dict):
        raise ValueError("equal_love must be a JSON object.")

    _apply_runtime_defaults(runtime)
    _validate_required(telegram, ["bot_token"], "telegram")
    room_chat_ids = telegram.get("room_chat_ids") or {}
    if not isinstance(room_chat_ids, dict):
        raise ValueError("telegram.room_chat_ids must be a JSON object.")
    telegram["room_chat_ids"] = {
        str(key).strip(): str(value).strip()
        for key, value in room_chat_ids.items()
        if str(key).strip() and str(value).strip() and not _is_placeholder(value)
    }
    if not telegram.get("chat_id") and not telegram.get("room_chat_ids"):
        raise ValueError("telegram.chat_id or telegram.room_chat_ids must be configured.")
    _validate_required(
        equal_love,
        [
            "username",
            "password",
            "x_request_verification_key",
            "x_artist_group_uuid",
        ],
        "equal_love",
    )

    return settings


def _apply_runtime_defaults(runtime: dict[str, Any]) -> None:
    data_dir = str(runtime.get("data_dir") or "data")
    runtime["data_dir"] = data_dir
    runtime.setdefault("auth_cache_path", str(Path(data_dir) / "auth_cache.json"))
    runtime.setdefault("state_db_path", str(Path(data_dir) / "state.db"))
    runtime.setdefault("poll_interval_seconds", 300)
    runtime.setdefault("page_size", 50)
    runtime.setdefault("max_pages_per_room", 5)
    runtime.setdefault("startup_replay_count", 2)
    runtime.setdefault("forward_history_on_first_run", False)


def _validate_required(section: dict[str, Any], keys: list[str], section_name: str) -> None:
    missing = [
        key
        for key in keys
        if not section.get(key) or _is_placeholder(section.get(key))
    ]
    if missing:
        raise ValueError(f"{section_name} is missing required fields: {', '.join(missing)}")


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and value.strip().startswith("<") and value.strip().endswith(">")
