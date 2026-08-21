"""PostgreSQL-Verbindung (Stammdaten, Logs – SPEC Kap. 2.1)."""

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_postgres_connection() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logger.info("PostgreSQL connection verified")


def init_db() -> None:
    # Modelle registrieren (create_all)
    from app.models import agent_workflow_config as _aw  # noqa: F401
    from app.models import inventory_folder as _if  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # Additive Schema-Updates ohne Alembic (bestehende DBs)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE conversation_artifacts "
                "ADD COLUMN IF NOT EXISTS meta JSONB"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE unprocessed_assets "
                "ADD COLUMN IF NOT EXISTS user_notes TEXT"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE unprocessed_assets "
                "ADD COLUMN IF NOT EXISTS ai_notes TEXT"
            )
        )
        # Bestehende notes → ai_notes (einmalig, nur wenn ai_notes noch leer)
        connection.execute(
            text(
                "UPDATE unprocessed_assets "
                "SET ai_notes = notes "
                "WHERE ai_notes IS NULL AND notes IS NOT NULL AND BTRIM(notes) <> ''"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE unprocessed_assets "
                "ADD COLUMN IF NOT EXISTS folder_id UUID REFERENCES inventory_folders(id) ON DELETE SET NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_unprocessed_assets_folder_id "
                "ON unprocessed_assets (folder_id)"
            )
        )
    try:
        from app.services.agent_workflow_db import ensure_standard_workflow

        ensure_standard_workflow()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Standard-Workflow konnte nicht geseedet werden: %s", exc)
    logger.info("PostgreSQL schema bereit (create_all + additive Alters)")
