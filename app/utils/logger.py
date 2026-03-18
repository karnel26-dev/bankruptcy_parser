"""
Настройка логирования.
Пишет одновременно в stdout и в файл с ротацией.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import config


def setup_logger(name: str = "bankruptcy_parser") -> logging.Logger:
    """
    Создаёт и настраивает логгер с выводом в файл и в stdout.

    Args:
        name: Имя логгера.

    Returns:
        Настроенный экземпляр logging.Logger.
    """
    logger = logging.getLogger(name)

    # Не добавлять handlers повторно при повторном вызове
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- Консоль ---
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # --- Файл с ротацией (10 МБ × 5 файлов) ---
    log_dir = os.path.dirname(config.LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
