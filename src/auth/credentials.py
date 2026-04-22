import json
import re
import uuid
from pathlib import Path
from typing import Optional

import requests

AUTH_BASE_URL = "https://api.entertainment-platform-auth.cosm.jp"
_RUNTIME_DEVICE_UUID: Optional[str] = None

_DEFAULT_HEADERS = {
    "user-agent": "io.cosm.fc.user.equal.love/1.3.0/iOS/26.4.1/iPhone",
    "accept-language": "ja",
    "accept-encoding": "gzip",
    "content-type": "application/json",
}


def _build_headers(
    device_uuid: str,
    x_request_verification_key: str,
    x_artist_group_uuid: str,
    authorization: Optional[str] = None,
) -> dict:
    headers = {
        **_DEFAULT_HEADERS,
        "x-request-verification-key": x_request_verification_key,
        "x-artist-group-uuid": x_artist_group_uuid,
        "x-device-uuid": device_uuid,
    }
    if authorization and not _is_placeholder(authorization):
        headers["authorization"] = f"Bearer {authorization}"
    return headers


def _load_json_file(path: str) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def _auth_config(config: dict) -> dict:
    equal_love_config = config.get("equal_love")
    if isinstance(equal_love_config, dict):
        return equal_love_config
    return config


def _save_json_file(data: dict, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"<.*>", value.strip()))


def _has_value(config: dict, key: str) -> bool:
    value = config.get(key)
    return bool(value) and not _is_placeholder(value)


def _generate_device_uuid() -> str:
    return f"ios_{uuid.uuid4()}"


def get_runtime_device_uuid(regenerate: bool = False) -> str:
    global _RUNTIME_DEVICE_UUID
    if regenerate or not _RUNTIME_DEVICE_UUID:
        _RUNTIME_DEVICE_UUID = _generate_device_uuid()
    return _RUNTIME_DEVICE_UUID


def validate_auth_config(config: dict, require_password: bool = False) -> None:
    required_fields = [
        "x_request_verification_key",
        "x_artist_group_uuid",
    ]
    if require_password:
        required_fields.extend(["username", "password"])

    missing = [field for field in required_fields if not _has_value(config, field)]
    if missing:
        raise ValueError(f"config is missing required fields: {', '.join(missing)}")


def load_auth_cache(cache_path: str) -> dict:
    try:
        cache = _load_json_file(cache_path)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{cache_path} is not valid JSON: {exc}") from exc

    return cache if isinstance(cache, dict) else {}


def load_runtime_auth(config_path: str, cache_path: str) -> dict:
    config = _auth_config(_load_json_file(config_path))
    cache = load_auth_cache(cache_path)
    runtime_auth = {**config, **cache}

    if not cache:
        for key in ["authorization", "refresh_token", "user_uuid", "is_verified"]:
            if _has_value(config, key):
                runtime_auth[key] = config[key]

    runtime_auth["x_device_uuid"] = get_runtime_device_uuid()
    return runtime_auth


def _extract_auth_payload(result: dict) -> dict:
    payload = result.get("data")
    if isinstance(payload, dict) and "accessToken" in payload:
        return payload
    return result


def _save_auth_payload(payload: dict, cache_path: str) -> str:
    cache = load_auth_cache(cache_path)
    cache["authorization"] = payload["accessToken"]

    if payload.get("refreshToken"):
        cache["refresh_token"] = payload["refreshToken"]
    if payload.get("uuid"):
        cache["user_uuid"] = payload["uuid"]
    if "isVerified" in payload:
        cache["is_verified"] = payload["isVerified"]

    _save_json_file(cache, cache_path)
    return cache["authorization"]


def login_with_password(
    username: str,
    password: str,
    device_uuid: str,
    x_request_verification_key: str,
    x_artist_group_uuid: str,
    authorization: Optional[str] = None,
) -> dict:
    response = requests.post(
        f"{AUTH_BASE_URL}/login",
        headers=_build_headers(
            device_uuid=device_uuid,
            x_request_verification_key=x_request_verification_key,
            x_artist_group_uuid=x_artist_group_uuid,
            authorization=authorization,
        ),
        json={
            "username": username,
            "password": password,
            "deviceUuid": device_uuid,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(
    refresh_token: str,
    device_uuid: str,
    x_request_verification_key: str,
    x_artist_group_uuid: str,
    authorization: Optional[str] = None,
) -> dict:
    response = requests.post(
        f"{AUTH_BASE_URL}/token/refresh",
        headers=_build_headers(
            device_uuid=device_uuid,
            x_request_verification_key=x_request_verification_key,
            x_artist_group_uuid=x_artist_group_uuid,
            authorization=authorization,
        ),
        json={
            "refreshToken": refresh_token,
            "deviceUuid": device_uuid,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def login_and_save(config_path: str, cache_path: str) -> str:
    config = _auth_config(_load_json_file(config_path))
    cache = load_auth_cache(cache_path)
    validate_auth_config(config, require_password=True)
    device_uuid = get_runtime_device_uuid(regenerate=True)

    result = login_with_password(
        username=config["username"],
        password=config["password"],
        device_uuid=device_uuid,
        x_request_verification_key=config["x_request_verification_key"],
        x_artist_group_uuid=config["x_artist_group_uuid"],
        authorization=cache.get("authorization") or config.get("authorization"),
    )
    return _save_auth_payload(_extract_auth_payload(result), cache_path)


def refresh_and_save(config_path: str, cache_path: str) -> str:
    config = _auth_config(_load_json_file(config_path))
    cache = load_auth_cache(cache_path)
    validate_auth_config(config)

    refresh_token = cache.get("refresh_token") or config.get("refresh_token")
    if not refresh_token:
        raise ValueError(f"{cache_path} is missing refresh_token")

    result = refresh_access_token(
        refresh_token=refresh_token,
        device_uuid=get_runtime_device_uuid(),
        x_request_verification_key=config["x_request_verification_key"],
        x_artist_group_uuid=config["x_artist_group_uuid"],
        authorization=cache.get("authorization") or config.get("authorization"),
    )
    return _save_auth_payload(_extract_auth_payload(result), cache_path)
