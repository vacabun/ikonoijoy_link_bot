from src.clients.base import BaseTalkClient
from src.clients.equal_love import EqualLoveClient
from src.clients.not_equal_me import NotEqualMeClient

CLIENT_CLASSES: dict[str, type[BaseTalkClient]] = {
    EqualLoveClient.APP_NAME: EqualLoveClient,
    NotEqualMeClient.APP_NAME: NotEqualMeClient,
}

APP_ALIASES = {
    alias: app_name
    for app_name, client_class in CLIENT_CLASSES.items()
    for alias in client_class.ALIASES
}

DEFAULT_APP = EqualLoveClient.APP_NAME
DEFAULT_USER_AGENT = EqualLoveClient.USER_AGENT


def normalize_app_name(app: str) -> str:
    normalized = str(app).strip().lower().replace(" ", "_")
    app_name = APP_ALIASES.get(normalized)
    if not app_name:
        choices = ", ".join(sorted(CLIENT_CLASSES))
        raise ValueError(f"Unknown talk app '{app}'. Expected one of: {choices}")
    return app_name


def client_class_for_app(app: str) -> type[BaseTalkClient]:
    return CLIENT_CLASSES[normalize_app_name(app)]


def app_profile(app: str) -> dict[str, str]:
    return client_class_for_app(app).profile()


def app_profile_from_base_url(base_url: str) -> dict[str, str]:
    normalized_url = base_url.rstrip("/")
    for client_class in CLIENT_CLASSES.values():
        if client_class.BASE_URL == normalized_url:
            return client_class.profile()
    return {
        "app": "",
        "base_url": normalized_url,
        "user_agent": DEFAULT_USER_AGENT,
    }


def create_client(
    *,
    app: str,
    authorization: str,
    x_request_verification_key: str,
    x_artist_group_uuid: str,
    x_device_uuid: str,
    base_url: str | None = None,
    user_agent: str | None = None,
) -> BaseTalkClient:
    client_class = client_class_for_app(app)
    return client_class(
        authorization=authorization,
        x_request_verification_key=x_request_verification_key,
        x_artist_group_uuid=x_artist_group_uuid,
        x_device_uuid=x_device_uuid,
        base_url=base_url,
        user_agent=user_agent,
    )
