"""Laufzeit-Einstellungen (z. B. API-Keys), die über die UI gepflegt werden
können, statt ausschließlich über `.env` (Nutzer-Feedback: 'API Key flexibel
als Eingabefeld im UI einfügen'). Einfacher Key-Value-Store."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
