from src.clients.base import BaseTalkClient


class EqualLoveClient(BaseTalkClient):
    APP_NAME = "equal_love"
    BASE_URL = "https://v3.api.equal-love.link.cosm.jp"
    USER_AGENT = "io.cosm.fc.user.equal.love/1.3.0/iOS/26.4.1/iPhone"
    ALIASES = ("equal_love", "equal-love", "equallove")
