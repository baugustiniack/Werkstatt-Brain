import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolStatus(str, enum.Enum):
    NEU = "neu"
    VERSCHLISSEN = "verschlissen"
    ABGEBROCHEN = "abgebrochen"


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    diameter_mm: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    flute_length_mm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    max_rpm: Mapped[int | None] = mapped_column(Integer)
    feed_rate_mm_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, name="tool_status", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ToolStatus.NEU,
        server_default=ToolStatus.NEU.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
