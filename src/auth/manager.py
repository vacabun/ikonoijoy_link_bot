import logging

from requests import HTTPError

from src.auth.credentials import (
    load_runtime_auth,
    login_and_save,
    refresh_and_save,
    validate_auth_config,
)
from src.clients.equal_love import EqualLoveClient

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Owns token loading/refresh and rebuilds EqualLoveClient when credentials change.
    """

    def __init__(self, config_path: str, cache_path: str):
        self._config_path = config_path
        self._cache_path = cache_path

    def build_client(self) -> EqualLoveClient:
        auth = load_runtime_auth(self._config_path, self._cache_path)
        validate_auth_config(auth)

        if not auth.get("authorization"):
            logger.info("Access token missing, logging in with username/password")
            login_and_save(self._config_path, self._cache_path)
            auth = load_runtime_auth(self._config_path, self._cache_path)

        return EqualLoveClient(
            authorization=auth["authorization"],
            x_request_verification_key=auth["x_request_verification_key"],
            x_artist_group_uuid=auth["x_artist_group_uuid"],
            x_device_uuid=auth["x_device_uuid"],
        )

    def refresh_client(self) -> EqualLoveClient:
        try:
            logger.info("Refreshing equal-love.link access token")
            refresh_and_save(self._config_path, self._cache_path)
        except (HTTPError, KeyError, ValueError) as exc:
            logger.warning("Token refresh failed, falling back to password login: %s", exc)
            login_and_save(self._config_path, self._cache_path)

        return self.build_client()
