import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExecutionLog(Base):
    """Interaktions-Log für den Meta-Coach (SPEC Kap. 2.1.1, 2.3, 2.5)."""

    __tablename__ = "execution_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects_cad.id", ondelete="SET NULL"), index=True
    )
    iteration: Mapped[int | None] = mapped_column(Integer)
    prompt: Mapped[str | None] = mapped_column(Text)
    generated_code: Mapped[str | None] = mapped_column(Text)
    sandbox_success: Mapped[bool | None] = mapped_column(Boolean)
    stdout: Mapped[str | None] = mapped_column(Text)
    stderr: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)
    user_corrections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    log_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evaluated_by_coach: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
