"""
Async воркер — координирует два парсера через asyncio.Queue.

Архитектура:
  - asyncio.gather() запускает fedresurs-задачи параллельно (semaphore = CONCURRENCY)
  - asyncio.Queue передаёт номера дел в KAD-консьюмер
  - KAD обрабатывает дела последовательно (Playwright не любит параллельные страницы)
  - Всё в одном event loop — никаких greenlet/threading проблем
"""
import asyncio

from app.config import config
from app.db.repository import (
    bulk_create_jobs,
    bulk_create_kad_jobs,
    get_legal_case_by_number,
    get_pending_jobs,
    get_pending_kad_jobs,
    mark_job,
    mark_kad_job,
    upsert_case_document,
    upsert_legal_case,
    upsert_person,
)
from app.db.session import get_session
from app.parser.fedresurs_client import FedresursClient
from app.parser.kad_client import KadClient
from app.utils.logger import logger

_SENTINEL = None


async def process_fedresurs_inn(
    inn: str,
    client: FedresursClient,
    kad_queue: asyncio.Queue,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, str | None]:
    """Обрабатывает один ИНН через fedresurs, кладёт дела в kad_queue."""
    async with semaphore:
        try:
            person_data = await client.find_person_by_inn(inn)
            if person_data is None:
                return inn, "not_found", None

            bankruptcy_data = await client.get_bankruptcy(
                guid=person_data["guid"], inn=inn
            )
            legal_cases_data = bankruptcy_data.get("legalCases", [])

            async with get_session() as session:
                person = await upsert_person(
                    session, inn=inn,
                    guid=person_data["guid"],
                    full_name=person_data["name"],
                )
                for case_data in legal_cases_data:
                    await upsert_legal_case(session, person=person, case_data=case_data)

            if not legal_cases_data:
                logger.info(f"[{inn}] fedresurs: дел не найдено")
                return inn, "not_found", None

            for case_data in legal_cases_data:
                case_number = case_data.get("number", "")
                if case_number:
                    await kad_queue.put(case_number)
                    logger.debug(f"[{inn}] → kad_queue: {case_number}")

            logger.info(f"[{inn}] fedresurs: сохранено дел: {len(legal_cases_data)}")
            return inn, "done", None

        except Exception as e:
            logger.error(f"[{inn}] fedresurs: ошибка: {e}", exc_info=True)
            return inn, "error", str(e)

        finally:
            await asyncio.sleep(config.DELAY_BETWEEN)


async def kad_consumer(kad_queue: asyncio.Queue, kad_client: KadClient) -> None:
    """
    Читает номера дел из очереди и обрабатывает их последовательно.
    Playwright не любит много параллельных страниц — sequential проще и надёжнее.
    """
    while True:
        case_number = await kad_queue.get()

        if case_number is _SENTINEL:
            logger.info("KAD consumer: получен сигнал завершения")
            break

        # Регистрируем задачу в БД
        async with get_session() as session:
            await bulk_create_kad_jobs(session, [case_number])

        status = "error"
        error_msg = None
        try:
            result = await kad_client.process_case(case_number)

            if result is None:
                status = "not_found"
            else:
                async with get_session() as session:
                    legal_case = await get_legal_case_by_number(session, case_number)
                    if legal_case is None:
                        logger.warning(f"[{case_number}] KAD: дело не найдено в БД")
                        status = "error"
                        error_msg = "legal_case not in db"
                    else:
                        await upsert_case_document(
                            session=session,
                            legal_case=legal_case,
                            document_data=result["document"],
                            pdf_content=result["pdf_content"],
                            download_url=result["download_url"],
                        )
                        logger.info(f"[{case_number}] KAD: документ сохранён")
                        status = "done"

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[{case_number}] KAD: ошибка: {e}", exc_info=True)
        finally:
            async with get_session() as session:
                await mark_kad_job(session, case_number=case_number,
                                   status=status, error_message=error_msg)

        await asyncio.sleep(config.DELAY_BETWEEN)

    logger.info("KAD consumer: завершён")


async def run_parser(inn_list: list[str]) -> None:
    """
    Запускает оба парсера в одном event loop.

    Fedresurs-задачи выполняются параллельно (asyncio.gather + Semaphore).
    KAD-задачи — последовательно в отдельной корутине (asyncio.create_task).
    Связь через asyncio.Queue.
    """
    async with get_session() as session:
        await bulk_create_jobs(session, inn_list)

    async with get_session() as session:
        pending_jobs = await get_pending_jobs(session)
        pending_inns = [j.inn for j in pending_jobs]

    async with get_session() as session:
        pending_kad = await get_pending_kad_jobs(session)
        pending_kad_numbers = [j.case_number for j in pending_kad]

    total = len(pending_inns)
    if total == 0 and not pending_kad_numbers:
        logger.info("Все задачи уже выполнены.")
        return

    logger.info(
        f"fedresurs: задач к обработке: {total} | "
        f"KAD resume: {len(pending_kad_numbers)} незакрытых дел"
    )

    kad_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    # Предзаполняем незакрытые KAD-задачи из предыдущего запуска
    for cn in pending_kad_numbers:
        await kad_queue.put(cn)

    async with FedresursClient() as fedresurs, KadClient() as kad:
        # Запускаем KAD-консьюмер как фоновую задачу
        kad_task = asyncio.create_task(kad_consumer(kad_queue, kad))

        # Запускаем fedresurs параллельно с ограничением конкурентности
        semaphore = asyncio.Semaphore(config.CONCURRENCY)
        done_count = error_count = not_found_count = 0

        tasks = [
            asyncio.create_task(
                process_fedresurs_inn(inn, fedresurs, kad_queue, semaphore)
            )
            for inn in pending_inns
        ]

        for coro in asyncio.as_completed(tasks):
            result_inn, status, error_msg = await coro

            async with get_session() as session:
                await mark_job(session, inn=result_inn, status=status,
                               error_message=error_msg)

            if status == "done":
                done_count += 1
            elif status == "not_found":
                not_found_count += 1
            else:
                error_count += 1

            processed = done_count + not_found_count + error_count
            logger.info(
                f"fedresurs прогресс: {processed}/{total} | "
                f"✓ {done_count} | не найдено: {not_found_count} | ошибок: {error_count}"
            )

        logger.info("fedresurs: все ИНН обработаны, ждём завершения KAD...")

        # Сигнал завершения для KAD-консьюмера
        await kad_queue.put(_SENTINEL)
        await kad_task

    logger.info("Парсинг полностью завершён.")
