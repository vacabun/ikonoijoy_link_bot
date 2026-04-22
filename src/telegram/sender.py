import io
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))
_PHOTO_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 50 * 1024 * 1024
_SEND_INTERVAL_SEC = 1.0
_LOG_PREVIEW_MAX_CHARS = 1200
_TRANSIENT_TELEGRAM_STATUS_CODES = {408, 425, 500, 502, 503, 504}


class TelegramSender:
    """
    Telegram Bot API wrapper with optional per-room routing.
    """

    def __init__(
        self,
        bot_token: str,
        default_chat_id: Optional[str] = None,
        system_chat_id: Optional[str] = None,
        room_chat_ids: Optional[dict[str, str]] = None,
    ):
        self._default_chat_id = default_chat_id.strip() if default_chat_id and default_chat_id.strip() else None
        self._system_chat_id = system_chat_id.strip() if system_chat_id and system_chat_id.strip() else None
        self._room_chat_ids = {
            str(key).strip(): str(value).strip()
            for key, value in (room_chat_ids or {}).items()
            if str(key).strip() and str(value).strip()
        }

        if not self._system_chat_id:
            self._system_chat_id = self._default_chat_id
        if not self._system_chat_id and self._room_chat_ids:
            self._system_chat_id = next(iter(self._room_chat_ids.values()))
        if not self._default_chat_id and not self._room_chat_ids:
            raise ValueError("telegram.chat_id or telegram.room_chat_ids must be configured.")

        self._api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session = requests.Session()

    def describe_routes(self) -> list[str]:
        routes = []
        for room_key, chat_id in self._room_chat_ids.items():
            routes.append(f"{room_key} -> {chat_id}")
        if self._default_chat_id:
            routes.append(f"default -> {self._default_chat_id}")
        return routes

    def send_message(self, room: dict, message: dict) -> None:
        chat_id = self._resolve_chat_id(room)
        header = self._format_header(room, message)
        text = self._normalize_text(message.get("textContent"))
        media_items = message.get("chatMedia") or []

        if not media_items:
            self._log_outgoing_message(
                chat_id=chat_id,
                room=room,
                message=message,
                send_type="text",
                payload=header if not text else f"{header}\n\n{text}",
            )
            self._send_text(chat_id, header, text)
            time.sleep(_SEND_INTERVAL_SEC)
            return

        caption = self._build_caption(header, text)
        for index, media in enumerate(media_items):
            self._log_outgoing_message(
                chat_id=chat_id,
                room=room,
                message=message,
                send_type=str(media.get("contentType") or "media"),
                payload=caption if index == 0 else "",
                media=media,
            )
            self._send_media(chat_id, media, caption if index == 0 else None)
            time.sleep(_SEND_INTERVAL_SEC)

    def send_system_notification(self, text: str) -> None:
        if not self._system_chat_id:
            logger.warning("Skip system notification because no system chat is configured")
            return

        try:
            self._send_text_raw(self._system_chat_id, f"[system]\n{text}")
        except Exception as exc:
            logger.error("Failed to send system notification: %s", exc)

    def _resolve_chat_id(self, room: dict) -> str:
        room_id = str(room.get("id", "")).strip()
        room_name = str(room.get("name", "")).strip()

        for key in [room_id, room_name]:
            if key and key in self._room_chat_ids:
                return self._room_chat_ids[key]

        if self._default_chat_id:
            return self._default_chat_id

        raise RuntimeError(f"No Telegram chat configured for room: {room_name or room_id}")

    def _send_media(self, chat_id: str, media: dict, caption: Optional[str]) -> None:
        url = media.get("url") or media.get("compressedUrl")
        if not url:
            self._send_text_raw(chat_id, caption or "[media url missing]")
            return

        content = self._download_media(url)
        content_type = str(media.get("contentType") or "").lower()
        extension = media.get("fileExtension") or "bin"
        filename = f"{media.get('id', 'media')}.{extension}"

        if content_type == "image" and len(content) <= _PHOTO_MAX_BYTES:
            self._post(
                "sendPhoto",
                data={"chat_id": chat_id, "caption": caption or ""},
                files={"photo": (filename, io.BytesIO(content), self._guess_mime_type(content_type, extension))},
            )
            return

        if content_type == "video" and len(content) <= _VIDEO_MAX_BYTES:
            self._post(
                "sendVideo",
                data={"chat_id": chat_id, "caption": caption or ""},
                files={"video": (filename, io.BytesIO(content), self._guess_mime_type(content_type, extension))},
            )
            return

        self._post(
            "sendDocument",
            data={"chat_id": chat_id, "caption": caption or ""},
            files={"document": (filename, io.BytesIO(content), self._guess_mime_type(content_type, extension))},
        )

    def _download_media(self, url: str) -> bytes:
        response = self._session.get(url, timeout=120)
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("Downloaded media is empty.")
        return response.content

    @staticmethod
    def _rewind_files(files: object) -> None:
        if not isinstance(files, dict):
            return

        for value in files.values():
            file_obj = value[1] if isinstance(value, tuple) and len(value) >= 2 else value
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)

    def _post(self, method: str, **kwargs) -> dict:
        for attempt in range(5):
            error_detail = "unknown error"
            try:
                self._rewind_files(kwargs.get("files"))
                response = self._session.post(
                    f"{self._api_base}/{method}",
                    timeout=60,
                    **kwargs,
                )
                try:
                    data = response.json()
                except ValueError:
                    data = {}

                if data.get("ok"):
                    return data

                error_detail = self._describe_telegram_error(response, data)
                if response.status_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram rate limit hit (%s). Retry after %s seconds", error_detail, retry_after)
                    time.sleep(retry_after)
                    continue

                if response.status_code not in _TRANSIENT_TELEGRAM_STATUS_CODES:
                    logger.error("Telegram API error: %s", error_detail)
                    raise RuntimeError(f"Telegram API error: {error_detail}")

                if attempt == 4:
                    logger.error("Telegram API retries exhausted: %s", error_detail)
                    raise RuntimeError(f"Telegram API error: {error_detail}")
            except requests.RequestException as exc:
                error_detail = str(exc)
                if attempt == 4:
                    raise RuntimeError(f"Telegram request failed: {exc}") from exc

            wait_seconds = (attempt + 1) * 3
            logger.warning("Telegram send failed (%s), retry in %s seconds", error_detail, wait_seconds)
            time.sleep(wait_seconds)

        raise RuntimeError("Telegram API retries exhausted")

    @staticmethod
    def _describe_telegram_error(response: requests.Response, data: dict) -> str:
        description = data.get("description") if isinstance(data, dict) else None
        if description:
            return f"HTTP {response.status_code}: {description}"

        text = response.text.strip()
        if text:
            text = text.replace("\n", " ")
            if len(text) > 200:
                text = text[:197] + "..."
            return f"HTTP {response.status_code}: {text}"

        return f"HTTP {response.status_code}: {response.reason or 'Unknown error'}"

    def _send_text(self, chat_id: str, header: str, text: str) -> None:
        payload = header if not text else f"{header}\n\n{text}"
        self._send_text_raw(chat_id, payload)

    def _send_text_raw(self, chat_id: str, text: str) -> None:
        if len(text) > 4096:
            text = text[:4090] + "\n..."

        self._post(
            "sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
        )

    @staticmethod
    def _log_outgoing_message(
        chat_id: str,
        room: dict,
        message: dict,
        send_type: str,
        payload: str,
        media: Optional[dict] = None,
    ) -> None:
        room_name = room.get("name") or message.get("postedUsername") or "unknown"
        preview = payload
        if len(preview) > _LOG_PREVIEW_MAX_CHARS:
            preview = preview[:_LOG_PREVIEW_MAX_CHARS] + "\n..."

        logger.info(
            "About to send Telegram message: chat_id=%s room=%s room_id=%s message_id=%s type=%s media_id=%s",
            chat_id,
            room_name,
            room.get("id"),
            message.get("id"),
            send_type,
            media.get("id") if media else None,
        )
        if preview:
            logger.info("Outgoing message preview:\n%s", preview)
        elif media:
            logger.info("Outgoing message preview: [media without caption]")

    @staticmethod
    def _format_header(room: dict, message: dict) -> str:
        room_name = str(room.get("name") or message.get("postedUsername") or "unknown").strip()
        posted_at = int(message.get("postedDate") or 0)
        if posted_at > 0:
            posted = datetime.fromtimestamp(posted_at, tz=_JST).strftime("%Y/%m/%d %H:%M:%S")
        else:
            posted = "unknown time"

        tag = room_name.replace(" ", "")
        return f"#{tag} {posted}"

    @staticmethod
    def _build_caption(header: str, text: str) -> str:
        caption = header if not text else f"{header}\n\n{text}"
        if len(caption) > 1024:
            caption = caption[:1020] + "\n..."
        return caption

    @staticmethod
    def _normalize_text(text: object) -> str:
        if not text:
            return ""
        return str(text).replace("\\r\\n", "\n").replace("\r\n", "\n").strip()

    @staticmethod
    def _guess_mime_type(content_type: str, extension: str) -> str:
        extension = extension.lower().lstrip(".")
        if content_type == "image":
            return "image/jpeg" if extension in {"jpg", "jpeg"} else f"image/{extension}"
        if content_type == "video":
            return "video/mp4" if extension == "mp4" else f"video/{extension}"
        return "application/octet-stream"
