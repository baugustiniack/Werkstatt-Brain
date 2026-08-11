"""Inventar-/Asset-Bibliothek (ehemals reine Crawler-Queue, SPEC Kap. 2.1.1,
2.3.1). Nutzer-Feedback: statt einer strukturierten Fräser-/Materialien-
Ansicht soll dies eine einfache, unstrukturierte Ablage für Dateien UND
manuelle Einträge sein, die per Volltext/Filter durchsucht und von der KI
(oder manuell) nachträglich strukturiert werden."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssetFileType(str, enum.Enum):
    IMAGE = "image"
    STEP = "step"
    STL = "stl"
    F3D = "f3d"
    PDF = "pdf"
    MANUAL = "manual"  # Manueller Eintrag ohne Anhang
    OTHER = "other"


class AssetStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class AssetSource(str, enum.Enum):
    CRAWLER = "crawler"
    UPLOAD = "upload"
    MANUAL = "manual"


class UnprocessedAsset(Base):
    __tablename__ = "unprocessed_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_path: Mapped[str | None] = mapped_column(String(512), unique=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    file_type: Mapped[AssetFileType] = mapped_column(
        Enum(AssetFileType, name="asset_file_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[AssetStatus] = mapped_column(
        Enum(AssetStatus, name="asset_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=AssetStatus.PENDING,
        server_default=AssetStatus.PENDING.value,
    )
    source: Mapped[AssetSource] = mapped_column(
        Enum(AssetSource, name="asset_source", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=AssetSource.CRAWLER,
        server_default=AssetSource.CRAWLER.value,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    # Legacy/Suchfeld: gespiegelt aus ai_notes (Kompatibilität für ältere Clients/Queries)
    notes: Mapped[str | None] = mapped_column(Text)
    # Nutzer-Beschreibung (manuell); fließt in die KI-Generierung ein, wird von der KI nicht überschrieben
    user_notes: Mapped[str | None] = mapped_column(Text)
    # KI-Beschreibung (Vision/LLM); User kann sie optional nachbearbeiten
    ai_notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    vision_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
