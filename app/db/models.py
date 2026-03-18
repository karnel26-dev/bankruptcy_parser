"""
Модели базы данных (SQLAlchemy 2.0).

Схема:
  persons          — физическое лицо (ФИО, ИНН, GUID на сайте)
  legal_cases      — дело о банкротстве, привязанное к persons
  case_documents   — документы дела с kad.arbitr.ru (PDF, дата, имя файла)
  parse_jobs       — журнал задач (resume-поддержка): статус обработки каждого ИНН
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Person(Base):
    """Физическое лицо из реестра."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inn: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    guid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    legal_cases: Mapped[list["LegalCase"]] = relationship(
        "LegalCase", back_populates="person", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Person inn={self.inn} name={self.full_name}>"


class LegalCase(Base):
    """Дело о банкротстве (fedresurs.ru)."""

    __tablename__ = "legal_cases"
    __table_args__ = (
        UniqueConstraint("person_id", "case_number", name="uq_person_case"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_guid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_number: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_publish_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_publish_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    person: Mapped["Person"] = relationship("Person", back_populates="legal_cases")
    documents: Mapped[list["CaseDocument"]] = relationship(
        "CaseDocument", back_populates="legal_case", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<LegalCase number={self.case_number} status={self.status_code}>"


class CaseDocument(Base):
    """
    Документ судебного дела (kad.arbitr.ru).
    Хранит метаданные и сам PDF-файл.
    Уникальность по (legal_case_id, document_id) — один документ не дублируется.
    """

    __tablename__ = "case_documents"
    __table_args__ = (
        UniqueConstraint("legal_case_id", "document_id", name="uq_case_document"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legal_case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("legal_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Идентификаторы из kad.arbitr.ru
    kad_case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Метаданные документа
    display_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ссылка на скачивание
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Сам PDF (бинарные данные)
    pdf_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pdf_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Флаг: удалось ли скачать файл
    is_downloaded: Mapped[bool] = mapped_column(default=False, nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    legal_case: Mapped["LegalCase"] = relationship("LegalCase", back_populates="documents")

    def __repr__(self) -> str:
        return f"<CaseDocument file={self.file_name} date={self.display_date}>"


class ParseJob(Base):
    """
    Журнал задач — фиксирует статус обработки каждого ИНН.
    Используется для resume: при перезапуске уже выполненные ИНН пропускаются.
    """

    __tablename__ = "parse_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inn: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
        # pending | done | not_found | error
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<ParseJob inn={self.inn} status={self.status}>"


class KadJob(Base):
    """
    Журнал задач для KAD-парсера.
    Аналог ParseJob, но по номеру дела а не ИНН.
    Нужен для resume: незакрытые KAD-задачи подхватываются при перезапуске.
    """

    __tablename__ = "kad_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
        # pending | done | not_found | error
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<KadJob case={self.case_number} status={self.status}>"
