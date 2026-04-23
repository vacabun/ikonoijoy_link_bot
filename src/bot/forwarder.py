import logging
import time
from typing import Callable

from requests import HTTPError

from src.auth.manager import AuthManager
from src.clients.equal_love import EqualLoveClient
from src.storage.state import StateManager
from src.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)


class EqualLoveForwardBot:
    """
    Poll equal-love.link talk rooms and forward new released messages to Telegram.
    """

    def __init__(
        self,
        auth_manager: AuthManager,
        sender: TelegramSender,
        state: StateManager,
        poll_interval: int = 300,
        page_size: int = 50,
        max_pages_per_room: int = 5,
        startup_replay_count: int = 2,
    ):
        self._auth_manager = auth_manager
        self._sender = sender
        self._state = state
        self._poll_interval = poll_interval
        self._page_size = page_size
        self._max_pages_per_room = max_pages_per_room
        self._startup_replay_count = startup_replay_count
        self._client = auth_manager.build_client()

    def run(self) -> None:
        notification_lines = [
            "Bot started",
            f"poll interval: {self._poll_interval} seconds",
            f"page size: {self._page_size}",
            f"max pages per room: {self._max_pages_per_room}",
            f"startup replay count: {self._startup_replay_count}",
        ]
        routes = self._sender.describe_routes()
        if routes:
            notification_lines.extend(["routes:"] + routes)
        self._sender.send_system_notification("\n".join(notification_lines))

        logger.info("=== Bot started ===")
        self.send_startup_messages()
        while True:
            started_at = time.time()
            sent_count = self.run_once()
            elapsed = time.time() - started_at
            logger.info(
                "Polling finished (sent=%d, elapsed=%.1fs, next=%ds)",
                sent_count,
                elapsed,
                self._poll_interval,
            )
            time.sleep(max(0, self._poll_interval - elapsed))

    def send_startup_messages(self) -> int:
        if self._startup_replay_count <= 0:
            return 0

        try:
            self._refresh_campaign_status()
            rooms = self._request_with_reauth(lambda client: self._list_accessible_rooms(client))
        except Exception as exc:
            logger.error("Failed to list talk rooms for startup replay: %s", exc, exc_info=True)
            return 0

        sent_count = 0
        logger.info("Sending latest %d messages on startup", self._startup_replay_count)
        for room in rooms:
            try:
                sent_count += self._send_latest_messages_for_room(room)
            except Exception as exc:
                logger.error(
                    "Failed to send startup messages for %s (id=%s): %s",
                    room.get("name"),
                    room.get("id"),
                    exc,
                    exc_info=True,
                )

        logger.info("Startup replay finished (sent=%d)", sent_count)
        return sent_count

    def _send_latest_messages_for_room(self, room: dict) -> int:
        room_id = int(room["id"])
        response = self._request_with_reauth(
            lambda client: client.get_chat(
                talk_room_id=room_id,
                page=1,
                page_size=max(self._startup_replay_count, self._page_size),
                page_start_id=0,
            )
        )
        messages = [
            message
            for message in response.get("data", [])
            if self._is_forwardable_message(message)
        ]
        latest_messages = sorted(
            messages,
            key=lambda item: (int(item.get("postedDate") or 0), int(item.get("id") or 0)),
            reverse=True,
        )[: self._startup_replay_count]

        sent_count = 0
        for message in sorted(latest_messages, key=lambda item: (int(item.get("postedDate") or 0), int(item.get("id") or 0))):
            self._sender.send_message(room, message)
            self._state.mark_sent(room_id, int(message["id"]))
            sent_count += 1

        logger.info("Startup replay sent %d messages for %s", sent_count, room.get("name", room_id))
        return sent_count

    def run_once(self) -> int:
        try:
            self._refresh_campaign_status()
            rooms = self._request_with_reauth(lambda client: self._list_accessible_rooms(client))
        except Exception as exc:
            logger.error("Failed to list talk rooms: %s", exc, exc_info=True)
            return 0

        logger.info("Accessible talk rooms to poll: %d", len(rooms))

        sent_count = 0
        for room in rooms:
            try:
                sent_count += self._poll_room(room)
            except Exception as exc:
                logger.error(
                    "Failed to poll room %s (id=%s): %s",
                    room.get("name"),
                    room.get("id"),
                    exc,
                    exc_info=True,
                )

        return sent_count

    def _poll_room(self, room: dict) -> int:
        room_id = int(room["id"])
        room_name = room.get("name", room_id)
        cursor = self._state.get_cursor(room_id)

        logger.info("Polling %s (id=%d) from postedDate>%d", room_name, room_id, cursor)
        messages = self._fetch_new_messages(room_id, cursor)
        if not messages:
            return 0

        sent_count = 0
        latest_posted_at = cursor
        for message in sorted(messages, key=lambda item: (int(item.get("postedDate") or 0), int(item.get("id") or 0))):
            message_id = int(message["id"])
            posted_at = int(message.get("postedDate") or 0)
            if self._state.is_sent(room_id, message_id):
                latest_posted_at = max(latest_posted_at, posted_at)
                continue

            self._sender.send_message(room, message)
            self._state.mark_sent(room_id, message_id)
            latest_posted_at = max(latest_posted_at, posted_at)
            sent_count += 1

        self._state.set_cursor(room_id, latest_posted_at)
        logger.info("Sent %d messages for %s", sent_count, room_name)
        return sent_count

    def _refresh_campaign_status(self) -> dict:
        response = self._request_with_reauth(lambda client: client.get_campaign())
        if response.get("isMaintenance"):
            logger.warning(
                "Campaign refresh completed during maintenance: %s",
                response.get("maintenanceMessage") or response.get("message") or "no message",
            )
        else:
            logger.info("Campaign refresh completed")
        return response

    def _fetch_new_messages(self, room_id: int, cursor: int) -> list[dict]:
        messages: list[dict] = []
        page_start_id = 0

        for page_index in range(self._max_pages_per_room):
            response = self._request_with_reauth(
                lambda client: client.get_chat(
                    talk_room_id=room_id,
                    page=1,
                    page_size=self._page_size,
                    page_start_id=page_start_id,
                )
            )
            page_messages = response.get("data") or []
            if not page_messages:
                break

            reached_old_messages = False
            for message in page_messages:
                if not self._is_forwardable_message(message):
                    continue

                message_id = int(message.get("id") or 0)
                posted_at = int(message.get("postedDate") or 0)
                if posted_at <= cursor:
                    reached_old_messages = True
                    continue
                if message_id and not self._state.is_sent(room_id, message_id):
                    messages.append(message)

            page_start_id = int(response.get("nextPageId") or 0)
            if reached_old_messages or page_start_id == 0:
                break

            logger.debug("Room %d fetched page %d, nextPageId=%d", room_id, page_index + 1, page_start_id)

        return messages

    @staticmethod
    def _list_accessible_rooms(client: EqualLoveClient) -> list[dict]:
        response = client.get_talk_rooms()
        data = response.get("data", {})
        total_unread = int(data.get("totalUnreadCount") or 0)
        total_unread_notifications = int(data.get("totalUnreadNotificationCount") or 0)
        not_arrived_notifications = data.get("notArrivedNotifications") or []
        rooms = data.get("talkRooms", [])

        logger.info(
            "Talk-room unread status: totalUnreadCount=%d, totalUnreadNotificationCount=%d, notArrivedNotifications=%d",
            total_unread,
            total_unread_notifications,
            len(not_arrived_notifications),
        )
        return [room for room in rooms if room.get("isAccessible")]

    @staticmethod
    def _is_forwardable_message(message: dict) -> bool:
        if message.get("isMine"):
            return False
        status = message.get("status")
        if status and status != "CHAT_STATUS_RELEASED":
            return False
        return bool(message.get("id"))

    def _request_with_reauth(self, request: Callable[[EqualLoveClient], dict | list]) -> dict | list:
        try:
            return request(self._client)
        except HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 401:
                raise

            logger.warning("equal-love.link token expired, refreshing and retrying once")
            self._client = self._auth_manager.refresh_client()
            return request(self._client)
