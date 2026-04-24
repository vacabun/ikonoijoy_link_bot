import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from requests import HTTPError

from src.auth.manager import AuthManager
from src.clients.equal_love import EqualLoveClient
from src.storage.state import StateManager
from src.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)


@dataclass
class BotAccount:
    name: str
    auth_manager: AuthManager
    client: EqualLoveClient


class EqualLoveForwardBot:
    """
    Poll equal-love.link talk rooms and forward new released messages to Telegram.
    """

    def __init__(
        self,
        auth_managers: list[AuthManager],
        sender: TelegramSender,
        state: StateManager,
        poll_interval: int = 300,
        page_size: int = 50,
        max_pages_per_room: int = 5,
        startup_backfill_hours: int = 48,
        startup_fallback_count: int = 2,
    ):
        self._accounts = [
            BotAccount(
                name=auth_manager.name,
                auth_manager=auth_manager,
                client=auth_manager.build_client(),
            )
            for auth_manager in auth_managers
        ]
        self._sender = sender
        self._state = state
        self._poll_interval = poll_interval
        self._page_size = page_size
        self._max_pages_per_room = max_pages_per_room
        self._startup_backfill_hours = startup_backfill_hours
        self._startup_fallback_count = startup_fallback_count

    def run(self) -> None:
        notification_lines = [
            "Bot started",
            f"accounts: {', '.join(account.name for account in self._accounts)}",
            f"poll interval: {self._poll_interval} seconds",
            f"page size: {self._page_size}",
            f"max pages per room: {self._max_pages_per_room}",
            f"startup backfill hours: {self._startup_backfill_hours}",
            f"startup fallback count: {self._startup_fallback_count}",
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
        if self._startup_backfill_hours <= 0:
            return 0

        try:
            rooms = self._collect_accessible_rooms()
        except Exception as exc:
            logger.error("Failed to list talk rooms for startup replay: %s", exc, exc_info=True)
            return 0

        sent_count = 0
        logger.info(
            "Sending unsent startup messages from the last %d hours",
            self._startup_backfill_hours,
        )
        for account, room in rooms:
            try:
                sent_count += self._send_latest_messages_for_room(account, room)
            except Exception as exc:
                logger.error(
                    "Failed to send startup messages for %s (id=%s) via %s: %s",
                    room.get("name"),
                    room.get("id"),
                    account.name,
                    exc,
                    exc_info=True,
                )

        logger.info("Startup replay finished (sent=%d)", sent_count)
        return sent_count

    def _send_latest_messages_for_room(self, account: BotAccount, room: dict) -> int:
        room_id = int(room["id"])
        cutoff_posted_at = self._startup_cutoff_posted_at()
        recent_messages = self._fetch_recent_messages(account, room_id, cutoff_posted_at)
        unsent_messages = [
            message
            for message in recent_messages
            if not self._state.is_sent(room_id, int(message["id"]))
        ]

        messages_to_send = unsent_messages
        mode = "backfill"
        if not messages_to_send and self._startup_fallback_count > 0:
            messages_to_send = self._fetch_latest_messages(account, room_id, self._startup_fallback_count)
            mode = "fallback"

        sent_count = 0
        for message in messages_to_send:
            self._sender.send_message(room, message)
            self._state.mark_sent(room_id, int(message["id"]))
            sent_count += 1

        logger.info(
            "Startup %s sent %d messages for %s via %s",
            mode,
            sent_count,
            room.get("name", room_id),
            account.name,
        )
        return sent_count

    def run_once(self) -> int:
        try:
            rooms = self._collect_accessible_rooms()
        except Exception as exc:
            logger.error("Failed to list talk rooms: %s", exc, exc_info=True)
            return 0

        logger.info("Accessible talk rooms to poll: %d", len(rooms))

        sent_count = 0
        for account, room in rooms:
            try:
                sent_count += self._poll_room(account, room)
            except Exception as exc:
                logger.error(
                    "Failed to poll room %s (id=%s) via %s: %s",
                    room.get("name"),
                    room.get("id"),
                    account.name,
                    exc,
                    exc_info=True,
                )

        return sent_count

    def _poll_room(self, account: BotAccount, room: dict) -> int:
        room_id = int(room["id"])
        room_name = room.get("name", room_id)
        cursor = self._state.get_cursor(room_id)

        logger.info("Polling %s (id=%d) via %s from postedDate>%d", room_name, room_id, account.name, cursor)
        messages = self._fetch_new_messages(account, room_id, cursor)
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
        logger.info("Sent %d messages for %s via %s", sent_count, room_name, account.name)
        return sent_count

    def _refresh_campaign_status(self, account: BotAccount) -> dict:
        response = self._request_with_reauth(account, lambda client: client.get_campaign())
        if response.get("isMaintenance"):
            logger.warning(
                "[%s] Campaign refresh completed during maintenance: %s",
                account.name,
                response.get("maintenanceMessage") or response.get("message") or "no message",
            )
        else:
            logger.info("[%s] Campaign refresh completed", account.name)
        return response

    def _fetch_new_messages(self, account: BotAccount, room_id: int, cursor: int) -> list[dict]:
        messages: list[dict] = []
        page_start_id = 0

        for page_index in range(self._max_pages_per_room):
            response = self._request_with_reauth(
                account,
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

            logger.debug("[%s] Room %d fetched page %d, nextPageId=%d", account.name, room_id, page_index + 1, page_start_id)

        return messages

    def _fetch_recent_messages(self, account: BotAccount, room_id: int, cutoff_posted_at: int) -> list[dict]:
        messages: list[dict] = []
        page_start_id = 0

        for page_index in range(self._max_pages_per_room):
            response = self._request_with_reauth(
                account,
                lambda client: client.get_chat(
                    talk_room_id=room_id,
                    page=1,
                    page_size=self._page_size,
                    page_start_id=page_start_id,
                ),
            )
            page_messages = response.get("data") or []
            if not page_messages:
                break

            reached_older_messages = False
            for message in page_messages:
                if not self._is_forwardable_message(message):
                    continue

                posted_at = int(message.get("postedDate") or 0)
                if posted_at < cutoff_posted_at:
                    reached_older_messages = True
                    continue

                messages.append(message)

            page_start_id = int(response.get("nextPageId") or 0)
            if reached_older_messages or page_start_id == 0:
                break

            logger.debug(
                "[%s] Startup backfill room %d fetched page %d, nextPageId=%d",
                account.name,
                room_id,
                page_index + 1,
                page_start_id,
            )

        return sorted(
            messages,
            key=lambda item: (int(item.get("postedDate") or 0), int(item.get("id") or 0)),
        )

    def _fetch_latest_messages(self, account: BotAccount, room_id: int, count: int) -> list[dict]:
        response = self._request_with_reauth(
            account,
            lambda client: client.get_chat(
                talk_room_id=room_id,
                page=1,
                page_size=max(count, 1),
                page_start_id=0,
            ),
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
        )[:count]
        return sorted(
            latest_messages,
            key=lambda item: (int(item.get("postedDate") or 0), int(item.get("id") or 0)),
        )

    def _startup_cutoff_posted_at(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._startup_backfill_hours)
        return int(cutoff.timestamp())

    @staticmethod
    def _list_accessible_rooms(account_name: str, client: EqualLoveClient) -> list[dict]:
        response = client.get_talk_rooms()
        data = response.get("data", {})
        total_unread = int(data.get("totalUnreadCount") or 0)
        total_unread_notifications = int(data.get("totalUnreadNotificationCount") or 0)
        not_arrived_notifications = data.get("notArrivedNotifications") or []
        rooms = data.get("talkRooms", [])

        logger.info(
            "[%s] Talk-room unread status: totalUnreadCount=%d, totalUnreadNotificationCount=%d, notArrivedNotifications=%d",
            account_name,
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

    def _collect_accessible_rooms(self) -> list[tuple[BotAccount, dict]]:
        room_providers: dict[int, tuple[BotAccount, dict]] = {}

        for account in self._accounts:
            try:
                self._refresh_campaign_status(account)
                rooms = self._request_with_reauth(
                    account,
                    lambda client, account_name=account.name: self._list_accessible_rooms(account_name, client),
                )
            except Exception as exc:
                logger.error(
                    "Failed to collect rooms for account %s: %s",
                    account.name,
                    exc,
                    exc_info=True,
                )
                continue

            for room in rooms:
                room_id = int(room["id"])
                if room_id not in room_providers:
                    room_providers[room_id] = (account, room)
                else:
                    logger.info(
                        "Room %s (id=%d) is accessible from multiple accounts; using %s",
                        room.get("name"),
                        room_id,
                        room_providers[room_id][0].name,
                    )

        return sorted(room_providers.values(), key=lambda item: int(item[1]["id"]))

    def _request_with_reauth(self, account: BotAccount, request: Callable[[EqualLoveClient], dict | list]) -> dict | list:
        try:
            return request(account.client)
        except HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 401:
                raise

            logger.warning("[%s] equal-love.link token expired, refreshing and retrying once", account.name)
            account.client = account.auth_manager.refresh_client()
            return request(account.client)
