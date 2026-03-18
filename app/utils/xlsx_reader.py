"""
Чтение ИНН из .xlsx файла.
Ожидается, что ИНН находится в первом столбце (A), начиная со второй строки.
Первая строка считается заголовком и пропускается.
"""
import openpyxl

from app.utils.logger import logger


def read_inn_list(filepath: str) -> list[str]:
    """
    Читает список ИНН из xlsx-файла.

    Args:
        filepath: Путь к .xlsx файлу.

    Returns:
        Список строк ИНН (дубликаты и пустые значения удалены).

    Raises:
        FileNotFoundError: Если файл не найден.
        ValueError: Если файл пустой или не содержит данных.
    """
    logger.info(f"Читаем список ИНН из файла: {filepath}")

    try:
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
    except FileNotFoundError:
        raise FileNotFoundError(f"Файл не найден: {filepath}")
    except Exception as e:
        raise ValueError(f"Ошибка чтения файла {filepath}: {e}")

    inn_list: list[str] = []
    seen: set[str] = set()

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        raw_value = row[0] if row else None
        if raw_value is None:
            continue

        inn = str(raw_value).strip()
        if not inn:
            continue

        # Удаляем дробную часть если Excel распознал как число (напр. 231138771115.0)
        if inn.endswith(".0"):
            inn = inn[:-2]

        if inn in seen:
            logger.warning(f"Строка {row_idx + 2}: дубликат ИНН {inn}, пропускаем")
            continue

        seen.add(inn)
        inn_list.append(inn)

    wb.close()
    logger.info(f"Загружено уникальных ИНН: {len(inn_list)}")
    return inn_list
