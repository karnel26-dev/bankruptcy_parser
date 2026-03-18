"""
Заглушка для прокси.
get_proxy() возвращает строку URL для aiohttp (proxy=...) или None.
"""
import random
from app.utils.logger import logger

PROXY_LIST: list[str] = [
    # "http://user:pass@proxy1.example.com:8080",
]


def get_proxy() -> str | None:
    if not PROXY_LIST:
        return None
    proxy_url = random.choice(PROXY_LIST)
    logger.debug(f"Прокси: {proxy_url}")
    return proxy_url
