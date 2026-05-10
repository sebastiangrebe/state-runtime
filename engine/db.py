"""Fact layer. Postgres is the only source of truth.

The model is never permitted to write here directly — engine intercepts
SQL it emits and runs it through these helpers.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (
    Column,
    Integer,
    String,
    create_engine,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://runtime:runtime@localhost:5432/runtime",
)


class Base(DeclarativeBase):
    pass


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message = Column(String(512), nullable=False)
    priority = Column(String(16), nullable=False, default="medium")
    status = Column(String(16), nullable=False, default="open")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "priority": self.priority,
            "status": self.status,
        }


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create tables, seed if empty."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    with session_scope() as s:
        existing = s.execute(select(Alert)).scalars().first()
        if existing is not None:
            return
        s.add_all(
            [
                Alert(message="CPU sustained at 94% on api-prod-3", priority="high", status="open"),
                Alert(message="Disk free below 8% on db-replica-1", priority="medium", status="open"),
                Alert(message="Cron job 'nightly-rollup' missed window", priority="low", status="open"),
            ]
        )


def list_alerts() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.execute(select(Alert).order_by(Alert.id)).scalars().all()
        return [r.to_dict() for r in rows]


SAFE_PREFIXES = ("UPDATE ALERTS", "INSERT INTO ALERTS", "DELETE FROM ALERTS")


def execute_sql(sql: str) -> dict[str, Any]:
    """Whitelist-guarded execution. Model output passes through here."""
    stripped = sql.strip().rstrip(";")
    upper = " ".join(stripped.upper().split())
    if not any(upper.startswith(p) for p in SAFE_PREFIXES):
        return {"ok": False, "error": f"Rejected SQL (prefix not whitelisted): {sql!r}"}
    try:
        with session_scope() as s:
            result = s.execute(text(stripped))
            rowcount = result.rowcount
        return {"ok": True, "rowcount": rowcount, "sql": stripped}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "sql": stripped}


if __name__ == "__main__":
    init_db()
    print(list_alerts())
