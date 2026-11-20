from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from adrobot.errors import AppError


class Base(DeclarativeBase):
    pass


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="running")
    step: Mapped[str] = mapped_column(String(40), default="validation")
    campaign_id: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


def make_engine(url: str) -> Engine:
    options = (
        {"check_same_thread": False, "timeout": 15} if url.startswith("sqlite") else {}
    )
    return create_engine(url, connect_args=options)


class OperationRepository:
    def __init__(self, engine: Engine) -> None:
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def get(self, operation_id: str) -> Operation:
        with self.sessions() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise AppError("Операция не найдена", 404)
            return operation

    def list_recent(self) -> list[Operation]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(Operation).order_by(Operation.created_at.desc()).limit(30)
                )
            )

    def claim(
        self, operation_id: str, fingerprint: str, request: dict[str, Any]
    ) -> tuple[Operation, bool]:
        with self.sessions() as session:
            operation = Operation(
                id=operation_id, fingerprint=fingerprint, request=request
            )
            session.add(operation)
            try:
                session.commit()
                return operation, True
            except IntegrityError:
                session.rollback()
        operation = self.get(operation_id)
        if operation.fingerprint != fingerprint:
            raise AppError("Этот ключ операции уже использован с другими данными", 409)
        return operation, False

    def update(self, operation_id: str, **values: Any) -> Operation:
        with self.sessions() as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise AppError("Операция не найдена", 404)
            for key, value in values.items():
                setattr(operation, key, value)
            operation.updated_at = datetime.now(UTC)
            session.commit()
            return operation
