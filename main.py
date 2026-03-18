"""
Точка входа — запускает asyncio event loop.
"""
import argparse
import asyncio
import sys

from app.config import config
from app.db.session import init_db
from app.parser.worker import run_parser
from app.utils.logger import logger
from app.utils.xlsx_reader import read_inn_list


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Парсер банкротств fedresurs.ru + kad.arbitr.ru")
    parser.add_argument(
        "--input",
        type=str,
        default=config.INPUT_FILE,
        help=f"Путь к .xlsx файлу со списком ИНН (по умолчанию: {config.INPUT_FILE})",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()

    logger.info("=" * 60)
    logger.info("Запуск парсера банкротств fedresurs.ru + kad.arbitr.ru")
    logger.info(f"Входной файл: {args.input}")
    logger.info(f"Конкурентность: {config.CONCURRENCY}")
    logger.info(f"Задержка между запросами: {config.DELAY_BETWEEN}s")
    logger.info("=" * 60)

    try:
        await init_db()
    except Exception as e:
        logger.critical(f"Не удалось подключиться к БД: {e}")
        sys.exit(1)

    try:
        inn_list = read_inn_list(args.input)
    except (FileNotFoundError, ValueError) as e:
        logger.critical(f"Ошибка чтения входного файла: {e}")
        sys.exit(1)

    if not inn_list:
        logger.warning("Список ИНН пустой. Завершение.")
        sys.exit(0)

    await run_parser(inn_list)


if __name__ == "__main__":
    asyncio.run(main_async())
