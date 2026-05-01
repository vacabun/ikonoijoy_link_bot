from urllib.parse import urlparse

import requests

_DEFAULT_HEADERS = {
    "accept-language": "ja",
    "accept-encoding": "gzip",
}

_DEFAULT_TIMEOUT = 30


class BaseTalkClient:
    """
    Base client for Cosm talk APIs.
    """

    APP_NAME = ""
    BASE_URL = ""
    USER_AGENT = ""
    ALIASES: tuple[str, ...] = ()

    def __init__(
        self,
        authorization: str,
        x_request_verification_key: str,
        x_artist_group_uuid: str,
        x_device_uuid: str,
        base_url: str | None = None,
        user_agent: str | None = None,
    ):
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.device_uuid = x_device_uuid
        self.session = requests.Session()
        self.session.headers.update(
            {
                **_DEFAULT_HEADERS,
                "user-agent": user_agent or self.USER_AGENT,
                "host": _host_from_base_url(self.base_url),
                "authorization": f"Bearer {authorization}",
                "x-request-verification-key": x_request_verification_key,
                "x-artist-group-uuid": x_artist_group_uuid,
                "x-device-uuid": x_device_uuid,
            }
        )

    @classmethod
    def profile(cls) -> dict[str, str]:
        return {
            "app": cls.APP_NAME,
            "base_url": cls.BASE_URL,
            "user_agent": cls.USER_AGENT,
        }

    def get_talk_rooms(self, page: int = 1) -> dict:
        response = self.session.get(
            f"{self.base_url}/user/v2/talk-room",
            params={"page": page},
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_campaign(self) -> dict:
        response = self.session.get(
            f"{self.base_url}/user/v1/campaign",
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_chat(
        self,
        talk_room_id: int,
        page: int = 1,
        page_size: int = 50,
        has_media: bool = False,
        is_favorite: bool = False,
        is_sent_fan_letter: bool = False,
        date_search_in_secs: int = 0,
        page_start_id: int = 0,
        order_by: int = 1,
    ) -> dict:
        response = self.session.get(
            f"{self.base_url}/user/v2/chat/{talk_room_id}",
            params={
                "page": page,
                "pageSize": page_size,
                "hasMedia": str(has_media).lower(),
                "isFavorite": str(is_favorite).lower(),
                "isSentFanLetter": str(is_sent_fan_letter).lower(),
                "dateSearchInSecs": date_search_in_secs,
                "pageStartId": page_start_id,
                "orderBy": order_by,
            },
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()


def _host_from_base_url(base_url: str) -> str:
    host = urlparse(base_url).netloc
    if not host:
        raise ValueError(f"Invalid talk API base_url: {base_url}")
    return host
