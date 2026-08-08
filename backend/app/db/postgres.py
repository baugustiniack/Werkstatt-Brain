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
    Base.metadata.create_all(bind=engine)
