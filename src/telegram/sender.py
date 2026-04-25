import io
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_JST = timezone(timedelta(hours=9))
_PHOTO_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_DOCUMENT_MAX_BYTES = 30 * 1024 * 1024
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
        room_chat_ids: Optional[dict[str, object]] = None,
    ):
        self._default_chat_id = default_chat_id.strip() if default_chat_id and default_chat_id.strip() else None
        self._system_chat_id = system_chat_id.strip() if system_chat_id and system_chat_id.strip() else None
        self._room_chat_ids = {
            str(key).strip(): targets
            for key, value in (room_chat_ids or {}).items()
            if str(key).strip() and (targets := self._normalize_chat_targets(value))
        }

        if not self._system_chat_id:
            self._system_chat_id = self._default_chat_id
        if not self._system_chat_id and self._room_chat_ids:
            self._system_chat_id = str(next(iter(self._room_chat_ids.values()))[0]["chat_id"])
        if not self._default_chat_id and not self._room_chat_ids:
            raise ValueError("telegram.chat_id or telegram.room_chat_ids must be configured.")

        self._api_base = f"https://api.telegram.org/bot{bot_token}"
        self._session = requests.Session()

    def describe_routes(self) -> list[str]:
        routes = []
        for room_key, targets in self._room_chat_ids.items():
            routes.append(f"{room_key} -> {', '.join(self._format_target(target) for target in targets)}")
        if self._default_chat_id:
            routes.append(f"default -> {self._default_chat_id}")
        return routes

    def send_message(self, room: dict, message: dict) -> None:
        try:
            targets = self._resolve_targets(room)
        except RuntimeError as exc:
            self._notify_missing_route(room, message, str(exc))
            raise

        header = self._format_header(room, message)
        text = self._normalize_text(message.get("textContent"))
        media_items = message.get("chatMedia") or []

        success_count = 0
        for target in targets:
            try:
                self._send_message_to_target(
                    target=target,
                    room=room,
                    message=message,
                    header=header,
                    text=text,
                    media_items=media_items,
                )
                success_count += 1
            except Exception as exc:
                logger.error(
                    "Failed to send Telegram message to target=%s: %s",
                    self._format_target(target),
                    exc,
                    exc_info=True,
                )

        if success_count == 0:
            raise RuntimeError("Failed to send Telegram message to every configured chat")

    def _send_message_to_target(
        self,
        target: dict[str, object],
        room: dict,
        message: dict,
        header: str,
        text: str,
        media_items: list[dict],
    ) -> None:
        if not media_items:
            self._log_outgoing_message(
                target=target,
                room=room,
                message=message,
                send_type="text",
                payload=header if not text else f"{header}\n\n{text}",
            )
            self._send_text(target, header, text)
            time.sleep(_SEND_INTERVAL_SEC)
            return

        caption = self._build_caption(header, text)
        self._log_outgoing_message(
            target=target,
            room=room,
            message=message,
            send_type=self._describe_media_send_type(media_items),
            payload=caption,
            media=media_items[0] if media_items else None,
            media_count=len(media_items),
        )
        self._send_media_items(target, media_items, caption)
        time.sleep(_SEND_INTERVAL_SEC)

    def send_system_notification(self, text: str) -> None:
        if not self._system_chat_id:
            logger.warning("Skip system notification because no system chat is configured")
            return

        try:
            self._send_text_raw(self._system_chat_id, f"[system]\n{text}")
        except Exception as exc:
            logger.error("Failed to send system notification: %s", exc)

    def _resolve_targets(self, room: dict) -> list[dict[str, object]]:
        room_id = str(room.get("id", "")).strip()
        room_name = str(room.get("name", "")).strip()

        for key in [room_id, room_name]:
            if key and key in self._room_chat_ids:
                return self._room_chat_ids[key]

        if self._default_chat_id:
            return [{"chat_id": self._default_chat_id}]

        raise RuntimeError(f"No Telegram chat configured for room: {room_name or room_id}")

    def _notify_missing_route(self, room: dict, message: dict, error: str) -> None:
        room_name = str(room.get("name") or message.get("postedUsername") or "unknown").strip()
        room_id = str(room.get("id") or "").strip() or "unknown"
        message_id = str(message.get("id") or "").strip() or "unknown"
        self.send_system_notification(
            "\n".join(
                [
                    "Telegram routing error",
                    error,
                    f"room: {room_name}",
                    f"room_id: {room_id}",
                    f"message_id: {message_id}",
                    "Set telegram.chat_id or add this room to telegram.room_chat_ids.",
                ]
            )
        )

    @staticmethod
    def _normalize_chat_targets(value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [
                target
                for item in value
                if (target := TelegramSender._normalize_chat_target(item))
            ]

        target = TelegramSender._normalize_chat_target(value)
        return [target] if target else []

    @staticmethod
    def _normalize_chat_target(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            chat_id = str(value.get("chat_id") or "").strip()
            if not chat_id:
                return {}
            target: dict[str, object] = {"chat_id": chat_id}
            thread_id = value.get("message_thread_id")
            if thread_id is not None and thread_id != "":
                target["message_thread_id"] = int(thread_id)
            return target

        chat_id = str(value).strip() if value is not None else ""
        return {"chat_id": chat_id} if chat_id else {}

    @staticmethod
    def _message_thread_id(target: dict[str, object]) -> Optional[int]:
        thread_id = target.get("message_thread_id")
        return int(thread_id) if thread_id is not None else None

    @staticmethod
    def _target_data(target: dict[str, object], **extra: object) -> dict[str, object]:
        data = {"chat_id": str(target["chat_id"])}
        thread_id = TelegramSender._message_thread_id(target)
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        data.update(extra)
        return data

    @staticmethod
    def _format_target(target: dict[str, object]) -> str:
        chat_id = str(target["chat_id"])
        thread_id = TelegramSender._message_thread_id(target)
        if thread_id is None:
            return chat_id
        return f"{chat_id}#topic:{thread_id}"

    def _send_media(self, target: dict[str, object], media: dict, caption: Optional[str]) -> None:
        prepared_media = self._prepare_media(media)
        self._send_prepared_media(target, prepared_media, caption)

    def _send_media_items(self, target: dict[str, object], media_items: list[dict], caption: str) -> None:
        if len(media_items) == 1:
            self._send_media(target, media_items[0], caption)
            return

        prepared_media_items = [self._prepare_media(media) for media in media_items]
        fallback_reason = "Media group contains unsupported items"
        if all(self._is_media_group_compatible(media) for media in prepared_media_items):
            try:
                for index, chunk in enumerate(self._chunks(prepared_media_items, 10)):
                    self._send_media_group(target, chunk, caption if index == 0 else None)
                    if index < (len(prepared_media_items) - 1) // 10:
                        time.sleep(_SEND_INTERVAL_SEC)
                return
            except RuntimeError as exc:
                if "HTTP 413" not in str(exc):
                    raise
                fallback_reason = "Media group is too large"

        logger.warning("%s, falling back to individual sends", fallback_reason)
        for index, media in enumerate(prepared_media_items):
            self._send_prepared_media(target, media, caption if index == 0 else None)
            if index < len(prepared_media_items) - 1:
                time.sleep(_SEND_INTERVAL_SEC)

    def _prepare_media(self, media: dict) -> dict:
        url = media.get("url") or media.get("compressedUrl")
        if not url:
            return {
                "source": media,
                "missing_url": True,
                "content": b"",
                "content_type": str(media.get("contentType") or "").lower(),
                "extension": media.get("fileExtension") or "bin",
                "filename": f"{media.get('id', 'media')}.{media.get('fileExtension') or 'bin'}",
                "mime_type": "application/octet-stream",
            }

        content = self._download_media(url)
        content_type = str(media.get("contentType") or "").lower()
        extension = media.get("fileExtension") or "bin"
        filename = f"{media.get('id', 'media')}.{extension}"
        return {
            "source": media,
            "missing_url": False,
            "content": content,
            "content_type": content_type,
            "extension": extension,
            "filename": filename,
            "mime_type": self._guess_mime_type(content_type, extension),
        }

    def _send_prepared_media(self, target: dict[str, object], media: dict, caption: Optional[str]) -> None:
        if media["missing_url"]:
            self._send_text_raw(
                str(target["chat_id"]),
                caption or "[media url missing]",
                message_thread_id=self._message_thread_id(target),
            )
            return

        if media["content_type"] == "image" and len(media["content"]) <= _PHOTO_MAX_BYTES:
            self._post(
                "sendPhoto",
                data=self._target_data(target, caption=caption or ""),
                files={"photo": (media["filename"], io.BytesIO(media["content"]), media["mime_type"])},
            )
            return

        if media["content_type"] == "image" and len(media["content"]) > _IMAGE_DOCUMENT_MAX_BYTES:
            raise RuntimeError(
                f"Image is larger than {_IMAGE_DOCUMENT_MAX_BYTES // 1024 // 1024}MB and cannot be sent"
            )

        if media["content_type"] == "video" and len(media["content"]) <= _VIDEO_MAX_BYTES:
            self._post(
                "sendVideo",
                data=self._target_data(target, caption=caption or ""),
                files={"video": (media["filename"], io.BytesIO(media["content"]), media["mime_type"])},
            )
            return

        self._post(
            "sendDocument",
            data=self._target_data(target, caption=caption or ""),
            files={"document": (media["filename"], io.BytesIO(media["content"]), media["mime_type"])},
        )

    def _send_media_group(
        self,
        target: dict[str, object],
        media_items: list[dict],
        caption: Optional[str],
    ) -> None:
        media_payload = []
        files = {}
        for index, media in enumerate(media_items):
            attach_name = f"media{index}"
            payload_item = {
                "type": "photo" if media["content_type"] == "image" else "video",
                "media": f"attach://{attach_name}",
            }
            if index == 0 and caption:
                payload_item["caption"] = caption
            media_payload.append(payload_item)
            files[attach_name] = (
                media["filename"],
                io.BytesIO(media["content"]),
                media["mime_type"],
            )

        self._post(
            "sendMediaGroup",
            data=self._target_data(target, media=json.dumps(media_payload, ensure_ascii=False)),
            files=files,
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

    def _send_text(self, target: dict[str, object], header: str, text: str) -> None:
        payload = header if not text else f"{header}\n\n{text}"
        self._send_text_raw(
            str(target["chat_id"]),
            payload,
            message_thread_id=self._message_thread_id(target),
        )

    def _send_text_raw(
        self,
        chat_id: str,
        text: str,
        message_thread_id: Optional[int] = None,
    ) -> None:
        if len(text) > 4096:
            text = text[:4090] + "\n..."

        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

        self._post(
            "sendMessage",
            json=payload,
        )

    @staticmethod
    def _log_outgoing_message(
        target: dict[str, object],
        room: dict,
        message: dict,
        send_type: str,
        payload: str,
        media: Optional[dict] = None,
        media_count: int = 0,
    ) -> None:
        room_name = room.get("name") or message.get("postedUsername") or "unknown"
        preview = payload
        if len(preview) > _LOG_PREVIEW_MAX_CHARS:
            preview = preview[:_LOG_PREVIEW_MAX_CHARS] + "\n..."

        logger.info(
            "About to send Telegram message: target=%s room=%s room_id=%s message_id=%s type=%s media_id=%s media_count=%d",
            TelegramSender._format_target(target),
            room_name,
            room.get("id"),
            message.get("id"),
            send_type,
            media.get("id") if media else None,
            media_count,
        )
        if preview:
            logger.info("Outgoing message preview:\n%s", preview)
        elif media:
            logger.info("Outgoing message preview: [media without caption]")

    @staticmethod
    def _describe_media_send_type(media_items: list[dict]) -> str:
        if len(media_items) > 1:
            content_types = sorted({str(media.get("contentType") or "media").lower() for media in media_items})
            return "media_group:" + ",".join(content_types)
        return str(media_items[0].get("contentType") or "media") if media_items else "media"

    @staticmethod
    def _is_media_group_compatible(media: dict) -> bool:
        if media["missing_url"]:
            return False
        if media["content_type"] == "image":
            return len(media["content"]) <= _PHOTO_MAX_BYTES
        if media["content_type"] == "video":
            return len(media["content"]) <= _VIDEO_MAX_BYTES
        return False

    @staticmethod
    def _chunks(items: list[dict], size: int) -> list[list[dict]]:
        return [items[index : index + size] for index in range(0, len(items), size)]

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
