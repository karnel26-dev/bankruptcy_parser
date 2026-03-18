"""
Async HTTP-клиент для kad.arbitr.ru.

Использует только aiohttp — Playwright не нужен.

Алгоритм скачивания PDF:
  1. GET /Document/Pdf/{caseId}/{docId}/{fileName}?isAddStamp=True
     → HTML с token и datat (whitespace steganography)
  2. Декодировать datat → имя wasm-функции
  3. GET /Wasm/api/v1/wasm_bg.wasm → wasm бинарник (кешируется)
  4. Вызвать wasm_функция(token) → hash
  5. POST /Document/Pdf/... body="token={token}&hash={hash}" → PDF

Поиск документов — через POST /Kad/SearchInstances (HTML-ответ) 
и GET /Kad/CaseDocumentsPage (JSON-ответ).
"""
import asyncio
import re
import time
import uuid
from functools import lru_cache

import aiohttp
from bs4 import BeautifulSoup

from app.config import config
from app.parser.kad_wasm import compute_hash, parse_challenge_html
from app.utils.logger import logger
from app.utils.proxy import get_proxy

KAD_BASE = config.KAD_BASE_URL
KAD_SEARCH = f"{KAD_BASE}/Kad/SearchInstances"
KAD_DOCS = f"{KAD_BASE}/Kad/CaseDocumentsPage"
# Правильный URL документа (из анализа браузерного трафика)
KAD_DOC_URL = f"{KAD_BASE}/Document/Pdf/{{kad_case_id}}/{{document_id}}/{{file_name}}?isAddStamp=True"
KAD_WASM_URL = f"{KAD_BASE}/Wasm/api/v1/wasm_bg.wasm"

_WASM_COOKIE = "53bc69d560d077b15c1f5a7e165f39e8"
_PR_FP = "a26597f1d4c95a31cb91ddec67cdd2bc8e1f36c2a91d25b677c4bffe845078ef"


class KadClient:
    """Async HTTP-клиент для kad.arbitr.ru (только aiohttp, без Playwright)."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._wasm_bytes: bytes | None = None  # кеш wasm бинарника

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
        """Прогрев сессии — три шага имитируют поведение браузера."""
        nav_headers = {
            "Host": "kad.arbitr.ru",
            "User-Agent": config.USER_AGENTS[0],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
        }
        proxy = get_proxy()
        try:
            async with self._session.get(KAD_BASE, headers=nav_headers, proxy=proxy) as r:
                logger.debug(f"KAD auth step 1: {r.status}")
            await asyncio.sleep(0.8)

            xhr = {**nav_headers,
                   "Referer": KAD_BASE + "/",
                   "Sec-Fetch-Dest": "empty",
                   "Sec-Fetch-Mode": "cors",
                   "Sec-Fetch-Site": "same-origin"}
            async with self._session.get(
                f"{KAD_BASE}/Content/Static/js/common/fp_bg.wasm?_=1705670688006",
                headers=xhr, proxy=proxy
            ) as r:
                logger.debug(f"KAD auth step 2 (wasm): {r.status}")
            await asyncio.sleep(0.3)

            async with self._session.get(
                f"{KAD_BASE}/manifest.kad.json",
                headers={**xhr, "Sec-Fetch-Dest": "manifest"},
                proxy=proxy
            ) as r:
                logger.debug(f"KAD auth step 3 (manifest): {r.status}")

            self._session.cookie_jar.update_cookies(
                {"wasm": _WASM_COOKIE, "rcid": str(uuid.uuid4()), "pr_fp": _PR_FP},
                response_url=aiohttp.client.URL(KAD_BASE),
            )

            cookies = {c.key for c in self._session.cookie_jar}
            logger.info(
                f"KAD HTTP: аутентификация завершена "
                f"(session={'ASP.NET_SessionId' in cookies}, "
                f"ddg={'__ddg1_' in cookies}, wasm={'wasm' in cookies})"
            )
        except Exception as e:
            logger.error(f"KAD HTTP: ошибка аутентификации: {e}")

    def _nav_headers(self, referer: str = "") -> dict:
        h = {
            "Host": "kad.arbitr.ru",
            "User-Agent": config.USER_AGENTS[0],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Priority": "u=0, i",
        }
        if referer:
            h["Referer"] = referer
        return h

    def _api_headers(self, referer: str) -> dict:
        return {
            "Host": "kad.arbitr.ru",
            "User-Agent": config.USER_AGENTS[0],
            "Accept": "*/*",
            "Accept-Language": "ru",
            "Content-Type": "application/json",
            "Origin": KAD_BASE,
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "X-Date-Format": "iso",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    async def _post_with_reauth(self, url: str, payload: dict, referer: str) -> str:
        proxy = get_proxy()
        for attempt in range(1, config.MAX_RETRIES + 1):
            async with self._session.post(
                url, json=payload, headers=self._api_headers(referer), proxy=proxy
            ) as resp:
                if resp.status == 451:
                    body = await resp.text(encoding="windows-1251", errors="replace")
                    logger.warning(f"KAD 451 (попытка {attempt}): {body[:100]}")
                    await self._authenticate()
                    await asyncio.sleep(2 * attempt)
                    continue
                resp.raise_for_status()
                return await resp.text()
        raise ValueError(f"KAD: исчерпаны попытки для {url}")

    async def find_case_id(self, case_number: str) -> str | None:
        payload = {
            "Page": 1, "Count": 25, "Courts": [], "DateFrom": None, "DateTo": None,
            "Sides": [], "Judges": [], "CaseNumbers": [case_number], "WithVKSInstances": False,
        }
        html = await self._post_with_reauth(KAD_SEARCH, payload, referer=KAD_BASE + "/")
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", class_="num_case")
        if not link or not link.get("href"):
            logger.info(f"[{case_number}] KAD: дело не найдено")
            return None
        match = re.search(r"/Card/([0-9a-f-]{36})", link["href"])
        if not match:
            return None
        kad_case_id = match.group(1)
        logger.info(f"[{case_number}] KAD: case_id={kad_case_id}")
        return kad_case_id

    async def get_documents(self, kad_case_id: str, case_number: str) -> list[dict]:
        url = (
            f"{KAD_DOCS}?_={int(time.time() * 1000)}"
            f"&caseId={kad_case_id}&page=1&perPage=25"
        )
        referer = f"{KAD_BASE}/Card/{kad_case_id}"
        proxy = get_proxy()
        for attempt in range(1, config.MAX_RETRIES + 1):
            async with self._session.get(
                url,
                headers={**self._api_headers(referer),
                         "Accept": "application/json, text/javascript, */*",
                         "Content-Type": "application/x-www-form-urlencoded"},
                proxy=proxy
            ) as resp:
                if resp.status == 451:
                    await self._authenticate()
                    await asyncio.sleep(2 * attempt)
                    continue
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                if not data.get("Success"):
                    return []
                items = data.get("Result", {}).get("Items", [])
                logger.info(f"[{case_number}] KAD: документов: {len(items)}")
                return items
        return []

    @staticmethod
    def get_latest_document(items: list[dict]) -> dict | None:
        if not items:
            return None

        def parse_ts(item: dict) -> int:
            raw = item.get("Date") or item.get("ActualDate") or ""
            try:
                if "/Date(" in str(raw):
                    return int(str(raw).replace("/Date(", "").replace(")/", ""))
                elif raw:
                    from datetime import datetime as _dt
                    return int(_dt.fromisoformat(str(raw)).timestamp() * 1000)
            except (ValueError, AttributeError):
                pass
            return 0

        return max(items, key=parse_ts)

    async def _get_wasm(self) -> bytes:
        """Скачивает wasm_bg.wasm (кешируется на всё время работы)."""
        if self._wasm_bytes is not None:
            return self._wasm_bytes

        proxy = get_proxy()
        async with self._session.get(
            KAD_WASM_URL,
            headers={
                "User-Agent": config.USER_AGENTS[0],
                "Accept": "*/*",
                "Referer": KAD_BASE + "/",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            },
            proxy=proxy,
        ) as resp:
            resp.raise_for_status()
            self._wasm_bytes = await resp.read()
            logger.info(f"KAD: wasm скачан, {len(self._wasm_bytes):,} байт")
            return self._wasm_bytes

    async def download_pdf(self, kad_case_id: str, document: dict, case_number: str) -> bytes | None:
        """
        Скачивает PDF через HTTP + wasm (без Playwright).

        Алгоритм:
          1. GET /Document/Pdf/... → HTML с token и datat
          2. Декодировать datat → имя wasm-функции
          3. Скачать wasm_bg.wasm (кешируется)
          4. Вычислить hash = wasm_функция(token)
          5. POST /Document/Pdf/... с token + hash → PDF
        """
        url = KAD_DOC_URL.format(
            kad_case_id=kad_case_id,
            document_id=document["Id"],
            file_name=document["FileName"],
        )
        proxy = get_proxy()

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                # Шаг 1: получаем HTML с JS-челленджем
                async with self._session.get(
                    url,
                    headers=self._nav_headers(),
                    proxy=proxy,
                ) as resp:
                    resp.raise_for_status()
                    html = await resp.text()

                ct = resp.headers.get("Content-Type", "")
                if "application/pdf" in ct:
                    # Повезло — сервер сразу отдал PDF (редко, но бывает)
                    async with self._session.get(url, headers=self._nav_headers(), proxy=proxy) as r2:
                        body = await r2.read()
                        logger.info(f"[{case_number}] KAD: PDF получен напрямую, {len(body):,} байт")
                        return body

                # Шаг 2: парсим token и salto, вычисляем hash = MD5(token+salto)
                try:
                    token_str, salto = parse_challenge_html(html)
                except ValueError as e:
                    logger.warning(f"[{case_number}] KAD: {e}")
                    if attempt < config.MAX_RETRIES:
                        await asyncio.sleep(2 * attempt)
                        continue
                    return None

                hash_val = compute_hash(token_str, salto)
                logger.debug(f"[{case_number}] KAD: token={token_str}, hash={hash_val}")

                # Шаг 5: POST с token + hash → PDF
                post_headers = {
                    **self._nav_headers(referer=url),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": KAD_BASE,
                }
                post_data = f"token={token_str}&hash={hash_val}"

                async with self._session.post(
                    url,
                    data=post_data,
                    headers=post_headers,
                    proxy=proxy,
                ) as post_resp:
                    post_ct = post_resp.headers.get("Content-Type", "")
                    body = await post_resp.read()
                    logger.debug(
                        f"[{case_number}] KAD POST: {post_resp.status} {post_ct!r} {len(body)}b"
                    )
                    if "pdf" in post_ct.lower() and len(body) > 1000:
                        logger.info(f"[{case_number}] KAD: PDF получен, {len(body):,} байт")
                        return body

                    # Если снова HTML — капча ещё активна, ждём
                    if attempt < config.MAX_RETRIES:
                        logger.warning(f"[{case_number}] KAD: получен HTML вместо PDF, ждём...")
                        await asyncio.sleep(5 * attempt)

            except Exception as e:
                logger.error(f"[{case_number}] KAD download error (попытка {attempt}): {e}")
                if attempt < config.MAX_RETRIES:
                    await asyncio.sleep(2 * attempt)

        logger.warning(f"[{case_number}] KAD: PDF не получен")
        return None

    async def process_case(self, case_number: str) -> dict | None:
        """Полный цикл обработки одного дела."""
        kad_case_id = await self.find_case_id(case_number)
        if not kad_case_id:
            return None

        items = await self.get_documents(kad_case_id, case_number)
        if not items:
            logger.info(f"[{case_number}] KAD: документы не найдены")
            return None

        latest = self.get_latest_document(items)
        if not latest:
            return None

        logger.info(
            f"[{case_number}] KAD: последний документ "
            f"{latest.get('FileName')} от {latest.get('DisplayDate')}"
        )

        pdf_content = await self.download_pdf(kad_case_id, latest, case_number)

        return {
            "kad_case_id": kad_case_id,
            "document": latest,
            "pdf_content": pdf_content,
            "download_url": KAD_DOC_URL.format(
                kad_case_id=kad_case_id,
                document_id=latest["Id"],
                file_name=latest["FileName"],
            ),
        }

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def __aenter__(self) -> "KadClient":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
