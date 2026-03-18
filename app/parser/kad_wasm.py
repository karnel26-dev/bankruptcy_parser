"""
Вычисление hash для скачивания PDF с kad.arbitr.ru.

Из анализа JS-кода в datat поле:
  hash = MD5(token + salto)
  где salto — содержимое <div id="salto">

Никакого WebAssembly не нужно — это обычный MD5 в Python.
wasm_bg.wasm используется сайтом для другого (fingerprinting),
но для вычисления hash документа достаточно hashlib.md5.
"""
from __future__ import annotations

import hashlib
import re


def parse_challenge_html(html: str) -> tuple[str, str]:
    """
    Извлекает token и salto из HTML JS-челленджа.

    Returns:
        (token_str, salto_str)

    Raises:
        ValueError: Если поля не найдены.
    """
    token_match = re.search(
        r'<input[^>]+id=["\']token["\'][^>]+value=["\']([^"\']+)["\']', html
    )
    if not token_match:
        token_match = re.search(r'id=["\']token["\'][^>]*value=["\']([^"\']+)["\']', html)

    salto_match = re.search(r'id=["\']salto["\'][^>]*>([^<]+)<', html)

    if not token_match:
        raise ValueError("Поле token не найдено в HTML")
    if not salto_match:
        raise ValueError("Поле salto не найдено в HTML")

    return token_match.group(1), salto_match.group(1)


def compute_hash(token: str, salto: str) -> str:
    """
    Вычисляет hash = MD5(token + salto).

    Восстановлено из JS: calc(token + salto)
    где calc() — стандартный MD5 с UTF-8 кодировкой.

    Args:
        token: Числовой токен из <input id="token">.
        salto: Строка из <div id="salto">.

    Returns:
        MD5 hex-строка, например 'd01f17cfd2610c119531defcd9207012'.
    """
    data = (token + salto).encode("utf-8")
    return hashlib.md5(data).hexdigest()
