from src.clients.base import BaseTalkClient


class NotEqualMeClient(BaseTalkClient):
    APP_NAME = "not_equal_me"
    BASE_URL = "https://v3.api.not-equal-me.link.cosm.jp"
    USER_AGENT = "io.cosm.fc.user.not.equal.me/1.3.1/iOS/26.4.2/iPhone"
    ALIASES = ("not_equal_me", "not-equal-me", "notequalme")
