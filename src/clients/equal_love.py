import requests

BASE_URL = "https://v3.api.equal-love.link.cosm.jp"

_DEFAULT_HEADERS = {
    "user-agent": "io.cosm.fc.user.equal.love/1.3.0/iOS/26.4.1/iPhone",
    "accept-language": "ja",
    "accept-encoding": "gzip",
    "host": "v3.api.equal-love.link.cosm.jp",
}

_DEFAULT_TIMEOUT = 30


class EqualLoveClient:
    """
    Minimal equal-love.link API client used by the Telegram forward bot.
    """

    def __init__(
        self,
        authorization: str,
        x_request_verification_key: str,
        x_artist_group_uuid: str,
        x_device_uuid: str,
    ):
        self.device_uuid = x_device_uuid
        self.session = requests.Session()
        self.session.headers.update(
            {
                **_DEFAULT_HEADERS,
                "authorization": f"Bearer {authorization}",
                "x-request-verification-key": x_request_verification_key,
                "x-artist-group-uuid": x_artist_group_uuid,
                "x-device-uuid": x_device_uuid,
            }
        )

    def get_talk_rooms(self, page: int = 1) -> dict:
        response = self.session.get(
            f"{BASE_URL}/user/v2/talk-room",
            params={"page": page},
            timeout=_DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_campaign(self) -> dict:
        response = self.session.get(
            f"{BASE_URL}/user/v1/campaign",
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
            f"{BASE_URL}/user/v2/chat/{talk_room_id}",
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
