from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # This project intentionally uses metadata creation instead of Alembic.
    # Keep the one additive migration here so existing Docker volumes receive
    # the idempotency column and its database-level uniqueness guarantee.
    inspector = inspect(engine)
    if "agent_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("agent_jobs")}
    with engine.begin() as connection:
        if "idempotency_key" not in columns:
            connection.execute(text("ALTER TABLE agent_jobs ADD COLUMN idempotency_key VARCHAR(64) NULL"))
        indexes = {index["name"] for index in inspector.get_indexes("agent_jobs")}
        indexes.update(constraint["name"] for constraint in inspector.get_unique_constraints("agent_jobs"))
        if "uq_agent_job_user_idempotency" not in indexes:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_agent_job_user_idempotency "
                    "ON agent_jobs (user_id, idempotency_key)"
                )
            )
