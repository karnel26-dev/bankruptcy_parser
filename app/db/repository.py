"""
Async репозиторий — все операции с базой данных.
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseDocument, KadJob, LegalCase, ParseJob, Person
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# ParseJob
# ---------------------------------------------------------------------------

async def bulk_create_jobs(session: AsyncSession, inn_list: list[str]) -> None:
    for inn in inn_list:
        stmt = (
            pg_insert(ParseJob)
            .values(inn=inn, status="pending", attempts=0)
            .on_conflict_do_nothing(index_elements=["inn"])
        )
        await session.execute(stmt)
    await session.commit()
    logger.info(f"Зарегистрировано задач: {len(inn_list)}")


async def get_pending_jobs(session: AsyncSession) -> list[ParseJob]:
    stmt = select(ParseJob).where(ParseJob.status.in_(["pending", "error"]))
    result = await session.scalars(stmt)
    return list(result.all())


async def mark_job(
    session: AsyncSession,
    inn: str,
    status: str,
    error_message: str | None = None,
) -> None:
    stmt = select(ParseJob).where(ParseJob.inn == inn)
    job = await session.scalar(stmt)
    if job:
        job.status = status
        job.error_message = error_message
        job.attempts += 1
        job.updated_at = datetime.utcnow()
        await session.commit()


# ---------------------------------------------------------------------------
# KadJob
# ---------------------------------------------------------------------------

async def bulk_create_kad_jobs(session: AsyncSession, case_numbers: list[str]) -> None:
    for case_number in case_numbers:
        stmt = (
            pg_insert(KadJob)
            .values(case_number=case_number, status="pending", attempts=0)
            .on_conflict_do_nothing(index_elements=["case_number"])
        )
        await session.execute(stmt)
    await session.commit()


async def get_pending_kad_jobs(session: AsyncSession) -> list[KadJob]:
    stmt = select(KadJob).where(KadJob.status.in_(["pending", "error"]))
    result = await session.scalars(stmt)
    return list(result.all())


async def mark_kad_job(
    session: AsyncSession,
    case_number: str,
    status: str,
    error_message: str | None = None,
) -> None:
    stmt = select(KadJob).where(KadJob.case_number == case_number)
    job = await session.scalar(stmt)
    if job:
        job.status = status
        job.error_message = error_message
        job.attempts += 1
        job.updated_at = datetime.utcnow()
        await session.commit()


# ---------------------------------------------------------------------------
# Person + LegalCase
# ---------------------------------------------------------------------------

async def upsert_person(
    session: AsyncSession, inn: str, guid: str, full_name: str
) -> Person:
    stmt = select(Person).where(Person.inn == inn)
    person = await session.scalar(stmt)
    if person is None:
        person = Person(inn=inn, guid=guid, full_name=full_name)
        session.add(person)
        await session.flush()
        logger.debug(f"Создана Person: {inn}")
    else:
        person.guid = guid
        person.full_name = full_name
        person.updated_at = datetime.utcnow()
    return person


async def upsert_legal_case(
    session: AsyncSession, person: Person, case_data: dict
) -> "LegalCase":
    case_number = case_data.get("number", "")
    status = case_data.get("status", {})
    publications = case_data.get("lastPublications", [])

    last_date: datetime | None = None
    last_type: str | None = None
    for pub in publications:
        date_str = pub.get("datePublish")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str)
            if last_date is None or dt > last_date:
                last_date = dt
                last_type = pub.get("reportTypeName") or pub.get("typeName")
        except ValueError:
            pass

    stmt = select(LegalCase).where(
        LegalCase.person_id == person.id,
        LegalCase.case_number == case_number,
    )
    legal_case = await session.scalar(stmt)

    if legal_case is None:
        legal_case = LegalCase(
            person_id=person.id,
            case_guid=case_data.get("guid"),
            case_number=case_number,
            status_code=status.get("code"),
            status_name=status.get("name"),
            last_publish_date=last_date,
            last_publish_type=last_type,
            raw_json=json.dumps(case_data, ensure_ascii=False),
        )
        session.add(legal_case)
        await session.flush()
        logger.debug(f"Создано дело: {case_number}")
    else:
        legal_case.status_code = status.get("code")
        legal_case.status_name = status.get("name")
        legal_case.last_publish_date = last_date
        legal_case.last_publish_type = last_type
        legal_case.raw_json = json.dumps(case_data, ensure_ascii=False)
        legal_case.updated_at = datetime.utcnow()

    return legal_case


# ---------------------------------------------------------------------------
# CaseDocument
# ---------------------------------------------------------------------------

async def get_legal_case_by_number(
    session: AsyncSession, case_number: str
) -> LegalCase | None:
    stmt = select(LegalCase).where(LegalCase.case_number == case_number)
    return await session.scalar(stmt)


async def upsert_case_document(
    session: AsyncSession,
    legal_case: LegalCase,
    document_data: dict,
    pdf_content: bytes | None = None,
    download_url: str | None = None,
) -> CaseDocument:
    document_id = document_data.get("Id", "")
    kad_case_id = document_data.get("CaseId", "")
    file_name = document_data.get("FileName", "")
    display_date = document_data.get("DisplayDate")
    document_type = document_data.get("DocumentTypeName")
    content_types = ", ".join(document_data.get("ContentTypes", []))

    document_date: datetime | None = None
    date_raw = document_data.get("Date") or document_data.get("ActualDate")
    if date_raw:
        try:
            if "/Date(" in str(date_raw):
                ts = int(str(date_raw).replace("/Date(", "").replace(")/", "")) / 1000
                document_date = datetime.utcfromtimestamp(ts)
            else:
                document_date = datetime.fromisoformat(str(date_raw))
        except (ValueError, TypeError):
            logger.warning(f"Не удалось распарсить дату: {date_raw!r}")

    stmt = select(CaseDocument).where(
        CaseDocument.legal_case_id == legal_case.id,
        CaseDocument.document_id == document_id,
    )
    doc = await session.scalar(stmt)

    if doc is None:
        doc = CaseDocument(
            legal_case_id=legal_case.id,
            kad_case_id=kad_case_id,
            document_id=document_id,
            display_date=display_date,
            document_date=document_date,
            file_name=file_name,
            document_type=document_type,
            content_types=content_types,
            download_url=download_url,
            pdf_content=pdf_content,
            pdf_size=len(pdf_content) if pdf_content else None,
            is_downloaded=pdf_content is not None,
        )
        session.add(doc)
    else:
        if pdf_content and not doc.is_downloaded:
            doc.pdf_content = pdf_content
            doc.pdf_size = len(pdf_content)
            doc.is_downloaded = True
            doc.download_url = download_url

    await session.flush()
    return doc
