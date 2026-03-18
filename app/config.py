"""
Конфигурация приложения.
Все параметры читаются из переменных окружения или .env файла.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Database — asyncpg вместо psycopg2
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://parser:parser@db:5432/bankruptcy"
    )

    # Parser
    CONCURRENCY: int = int(os.getenv("CONCURRENCY", "3"))
    DELAY_BETWEEN: float = float(os.getenv("DELAY_BETWEEN", "2.0"))
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

    # Input
    INPUT_FILE: str = os.getenv("INPUT_FILE", "/data/inn_list.xlsx")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.getenv("LOG_FILE", "/logs/parser.log")

    # Fedresurs URLs
    BASE_URL: str = "https://fedresurs.ru"
    PERSONS_FAST_URL: str = "https://fedresurs.ru/backend/persons/fast"
    BANKRUPTCY_URL: str = "https://fedresurs.ru/backend/persons/{guid}/bankruptcy"

    # KAD URLs
    KAD_BASE_URL: str = "https://kad.arbitr.ru"

    # User-Agent rotation
    USER_AGENTS: list[str] = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ]


config = Config()
