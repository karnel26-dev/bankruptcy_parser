"""
Async HTTP-клиент для fedresurs.ru.

Использует aiohttp.ClientSession.
Qrator обходится двумя GET-запросами — cookies сохраняются в сессии автоматически.
"""
import asyncio
import random

import aiohttp

from app.config import config
from app.utils.logger import logger
from app.utils.proxy import get_proxy


class FedresursClient:
    """
    Async HTTP-клиент для fedresurs.ru.
    Один экземпляр на всё приложение — сессия переиспользуется.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        ssl_ctx = __import__("ssl").create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = __import__("ssl").CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT),
        )
        await self._authenticate()

    async def _authenticate(self) -> None:
        """Двухшаговое прохождение Qrator."""
        headers = {
            "Host": "fedresurs.ru",
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
        }
        proxy = get_proxy()
        try:
            async with self._session.get(
                config.BASE_URL, headers=headers, proxy=proxy
            ) as r1:
                logger.debug(f"Fedresurs auth step 1: {r1.status}")

            await asyncio.sleep(0.5)

            headers2 = {**headers, "Sec-Fetch-Site": "same-origin"}
            async with self._session.get(
                config.BASE_URL, headers=headers2, proxy=proxy
            ) as r2:
                logger.debug(f"Fedresurs auth step 2: {r2.status}")

            cookies = {c.key for c in self._session.cookie_jar}
            if "qrator_jsid2" in cookies:
                logger.info("Fedresurs: аутентификация успешна")
            else:
                logger.warning("Fedresurs: qrator_jsid2 не получен, продолжаем")
        except Exception as e:
            logger.error(f"Fedresurs: ошибка аутентификации: {e}")

    async def _get_json(self, url: str, params: dict | None = None, referer: str = "") -> dict:
        headers = {
            "Host": "fedresurs.ru",
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Referer": referer or config.BASE_URL + "/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "ru",
        }
        proxy = get_proxy()
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                async with self._session.get(
                    url, params=params, headers=headers, proxy=proxy
                ) as resp:
                    if resp.status == 404:
                        raise ValueError(f"404: {url}")
                    if resp.status == 200:
                        ct = resp.headers.get("Content-Type", "")
                        if "application/json" not in ct:
                            raise ValueError(f"Ожидался JSON, получен {ct!r}")
                        return await resp.json(content_type=None)
                    resp.raise_for_status()
            except (aiohttp.ClientError, ValueError) as e:
                if attempt == config.MAX_RETRIES:
                    raise
                wait = 2 ** attempt
                logger.warning(f"Fedresurs: ошибка (попытка {attempt}): {e}, жду {wait}с")
                await asyncio.sleep(wait)
        raise RuntimeError("Fedresurs: исчерпаны попытки")

    async def find_person_by_inn(self, inn: str) -> dict | None:
        data = await self._get_json(
            config.PERSONS_FAST_URL,
            params={"searchString": inn},
            referer=config.BASE_URL + "/",
        )
        page_data = data.get("pageData", [])
        if not page_data:
            logger.info(f"[{inn}] Персона не найдена в реестре")
            return None
        person = page_data[0]
        logger.info(f"[{inn}] Найдено: {person.get('name')} guid={person.get('guid')}")
        return {"guid": person.get("guid"), "name": person.get("name"), "inn": inn}

    async def get_bankruptcy(self, guid: str, inn: str = "") -> dict:
        url = config.BANKRUPTCY_URL.format(guid=guid)
        data = await self._get_json(url, referer=f"{config.BASE_URL}/persons/{guid}")
        cases = data.get("legalCases", [])
        extrajudicial = data.get("extrajudicialBankruptcy", [])
        if not cases and not extrajudicial:
            logger.info(f"[{inn}] Данные о банкротстве отсутствуют")
        else:
            logger.info(f"[{inn}] Дел о банкротстве: {len(cases)} (внесудебных: {len(extrajudicial)})")
        return data

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def __aenter__(self) -> "FedresursClient":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
