import json
from pathlib import Path
from typing import Any

from src.clients.registry import (
    app_profile,
    app_profile_from_base_url,
    normalize_app_name,
)


def load_settings(config_path: str = "config.json") -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as file:
        settings = json.load(file)

    if not isinstance(settings, dict):
        raise ValueError("config.json must be a JSON object.")

    settings.setdefault("runtime", {})
    settings.setdefault("telegram", {})
    settings.setdefault("equal_love", {})
    settings.setdefault("equal_love_accounts", [])
    settings.setdefault("accounts", [])

    runtime = settings["runtime"]
    telegram = settings["telegram"]
    equal_love = settings["equal_love"]
    equal_love_accounts = settings["equal_love_accounts"]
    accounts = settings["accounts"]

    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a JSON object.")
    if not isinstance(telegram, dict):
        raise ValueError("telegram must be a JSON object.")
    if not isinstance(equal_love, dict):
        raise ValueError("equal_love must be a JSON object.")
    if not isinstance(equal_love_accounts, list):
        raise ValueError("equal_love_accounts must be a JSON array.")
    if not isinstance(accounts, list):
        raise ValueError("accounts must be a JSON array.")

    state_db_path_configured = bool(runtime.get("state_db_path")) and not _is_placeholder(runtime.get("state_db_path"))
    _apply_runtime_defaults(runtime)
    _validate_required(telegram, ["bot_token"], "telegram")
    room_chat_ids = telegram.get("room_chat_ids") or {}
    if not isinstance(room_chat_ids, dict):
        raise ValueError("telegram.room_chat_ids must be a JSON object.")
    telegram["room_chat_ids"] = {
        str(key).strip(): chat_ids
        for key, value in room_chat_ids.items()
        if str(key).strip() and (chat_ids := _normalize_chat_ids(value))
    }
    if not telegram.get("chat_id") and not telegram.get("room_chat_ids"):
        raise ValueError("telegram.chat_id or telegram.room_chat_ids must be configured.")

    normalized_accounts = _normalize_equal_love_accounts(
        accounts=equal_love_accounts or accounts,
        legacy_account=equal_love,
        runtime=runtime,
    )
    if not normalized_accounts:
        raise ValueError("At least one equal-love account must be configured.")
    _apply_state_db_default(
        runtime=runtime,
        accounts=normalized_accounts,
        config_path=config_path,
        state_db_path_configured=state_db_path_configured,
    )
    settings["equal_love_accounts"] = normalized_accounts

    return settings


def _apply_runtime_defaults(runtime: dict[str, Any]) -> None:
    data_dir = str(runtime.get("data_dir") or "data")
    runtime["data_dir"] = data_dir
    runtime.setdefault("auth_cache_path", str(Path(data_dir) / "auth_cache.json"))
    runtime.setdefault("auth_cache_dir", str(Path(data_dir) / "auth"))
    runtime.setdefault("poll_interval_seconds", 300)
    runtime.setdefault("page_size", 50)
    runtime.setdefault("max_pages_per_room", 5)
    runtime.setdefault("startup_backfill_hours", 48)
    runtime.setdefault("startup_fallback_count", 2)
    runtime.setdefault("forward_history_on_first_run", False)


def _apply_state_db_default(
    *,
    runtime: dict[str, Any],
    accounts: list[dict[str, Any]],
    config_path: str,
    state_db_path_configured: bool,
) -> None:
    if state_db_path_configured:
        return

    data_dir = Path(str(runtime["data_dir"]))
    apps = sorted({str(account.get("app") or "equal_love") for account in accounts})
    if len(apps) == 1:
        state_name = f"state.{apps[0]}.db"
    else:
        state_name = f"state.{_slugify_account_name(Path(config_path).stem)}.db"
    runtime["state_db_path"] = str(data_dir / state_name)


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


def _normalize_chat_ids(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            target
            for item in value
            if (target := _normalize_chat_target(item))
        ]

    target = _normalize_chat_target(value)
    return [target] if target else []


def _normalize_chat_target(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        chat_id = _normalize_chat_id(value.get("chat_id"))
        if not chat_id:
            return {}

        target: dict[str, Any] = {"chat_id": chat_id}
        thread_id = _normalize_message_thread_id(value.get("message_thread_id"))
        if thread_id is not None:
            target["message_thread_id"] = thread_id
        return target

    chat_id = _normalize_chat_id(value)
    return {"chat_id": chat_id} if chat_id else {}


def _normalize_chat_id(value: object) -> str:
    if value is None or _is_placeholder(value):
        return ""
    return str(value).strip()


def _normalize_message_thread_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _normalize_equal_love_accounts(
    *,
    accounts: list[Any],
    legacy_account: dict[str, Any],
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized_accounts: list[dict[str, Any]] = []
    source_accounts = accounts if accounts else ([legacy_account] if legacy_account else [])

    for index, account in enumerate(source_accounts, start=1):
        if not isinstance(account, dict):
            raise ValueError("Each equal-love account must be a JSON object.")

        normalized_account = dict(account)
        _validate_required(
            normalized_account,
            [
                "username",
                "password",
                "x_request_verification_key",
                "x_artist_group_uuid",
            ],
            f"equal_love_accounts[{index - 1}]",
        )

        name = str(normalized_account.get("name") or f"account-{index}").strip()
        normalized_account["name"] = name
        app = normalized_account.get("app")
        base_url = normalized_account.get("base_url")
        if app and not _is_placeholder(app):
            profile_name = normalize_app_name(str(app))
            profile = app_profile(profile_name)
        elif base_url and not _is_placeholder(base_url):
            profile = app_profile_from_base_url(str(base_url))
            profile_name = ""
        else:
            profile_name = "equal_love"
            profile = app_profile(profile_name)
        normalized_account["base_url"] = str(profile["base_url"]).strip().rstrip("/")
        user_agent = normalized_account.get("user_agent")
        if not user_agent or _is_placeholder(user_agent):
            user_agent = profile["user_agent"]
        normalized_account["user_agent"] = str(user_agent).strip()
        if profile_name:
            normalized_account["app"] = profile_name

        cache_path = normalized_account.get("cache_path")
        if not cache_path or _is_placeholder(cache_path):
            if len(source_accounts) == 1 and runtime.get("auth_cache_path"):
                cache_path = runtime["auth_cache_path"]
            else:
                cache_dir = Path(str(runtime["auth_cache_dir"]))
                cache_path = str(cache_dir / f"{_slugify_account_name(name)}.json")
        normalized_account["cache_path"] = str(cache_path)
        normalized_accounts.append(normalized_account)

    return normalized_accounts


def _slugify_account_name(value: str) -> str:
    slug_chars = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    slug = "".join(slug_chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "account"
