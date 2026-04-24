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

    def __init__(self, auth_config: dict, cache_path: str, name: str):
        self._auth_config = dict(auth_config)
        self._cache_path = cache_path
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def build_client(self) -> EqualLoveClient:
        auth = load_runtime_auth(self._auth_config, self._cache_path, device_key=self._name)
        validate_auth_config(auth)

        if not auth.get("authorization"):
            logger.info("[%s] Access token missing, logging in with username/password", self._name)
            login_and_save(self._auth_config, self._cache_path, device_key=self._name)
            auth = load_runtime_auth(self._auth_config, self._cache_path, device_key=self._name)

        return EqualLoveClient(
            authorization=auth["authorization"],
            x_request_verification_key=auth["x_request_verification_key"],
            x_artist_group_uuid=auth["x_artist_group_uuid"],
            x_device_uuid=auth["x_device_uuid"],
        )

    def refresh_client(self) -> EqualLoveClient:
        try:
            logger.info("[%s] Refreshing equal-love.link access token", self._name)
            refresh_and_save(self._auth_config, self._cache_path, device_key=self._name)
        except (HTTPError, KeyError, ValueError) as exc:
            logger.warning("[%s] Token refresh failed, falling back to password login: %s", self._name, exc)
            login_and_save(self._auth_config, self._cache_path, device_key=self._name)

        return self.build_client()
