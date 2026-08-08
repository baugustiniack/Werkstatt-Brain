import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProjectCad(Base):
    __tablename__ = "projects_cad"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_prompt: Mapped[str | None] = mapped_column(Text)
    requirements_contract: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generated_code: Mapped[str | None] = mapped_column(Text)
    step_file_path: Mapped[str | None] = mapped_column(String(512))
    stl_file_path: Mapped[str | None] = mapped_column(String(512))
    gcode_file_path: Mapped[str | None] = mapped_column(String(512))
    human_rating: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
