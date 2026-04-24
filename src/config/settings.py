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
    settings.setdefault("equal_love_accounts", [])

    runtime = settings["runtime"]
    telegram = settings["telegram"]
    equal_love = settings["equal_love"]
    equal_love_accounts = settings["equal_love_accounts"]

    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a JSON object.")
    if not isinstance(telegram, dict):
        raise ValueError("telegram must be a JSON object.")
    if not isinstance(equal_love, dict):
        raise ValueError("equal_love must be a JSON object.")
    if not isinstance(equal_love_accounts, list):
        raise ValueError("equal_love_accounts must be a JSON array.")

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

    normalized_accounts = _normalize_equal_love_accounts(
        accounts=equal_love_accounts,
        legacy_account=equal_love,
        runtime=runtime,
    )
    if not normalized_accounts:
        raise ValueError("At least one equal-love account must be configured.")
    settings["equal_love_accounts"] = normalized_accounts

    return settings


def _apply_runtime_defaults(runtime: dict[str, Any]) -> None:
    data_dir = str(runtime.get("data_dir") or "data")
    runtime["data_dir"] = data_dir
    runtime.setdefault("auth_cache_path", str(Path(data_dir) / "auth_cache.json"))
    runtime.setdefault("auth_cache_dir", str(Path(data_dir) / "auth"))
    runtime.setdefault("state_db_path", str(Path(data_dir) / "state.db"))
    runtime.setdefault("poll_interval_seconds", 300)
    runtime.setdefault("page_size", 50)
    runtime.setdefault("max_pages_per_room", 5)
    runtime.setdefault("startup_backfill_hours", 48)
    runtime.setdefault("startup_fallback_count", 2)
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
