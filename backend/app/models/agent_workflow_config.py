"""Persistente Agent-Workflow-Konfigurationen (User-editierbar).

- `Standard` ist fest in der DB und jederzeit reaktivierbar.
- Fixe Agenten: nur `supervisor` (immer enabled).
- Flexible Specialist und Inventory Manager sind optional zuschaltbar.
- Zwei Leer-Agenten (`custom_agent_1/2`) nur über Guidance; in Standard deaktiviert.
- Meta-Coach schreibt hier nicht hinein – nur der User (UI/API).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FIXED_AGENT_IDS = frozenset({"supervisor"})
STANDARD_WORKFLOW_NAME = "Standard"
# In Standard-Konfig deaktiviert (nur per User-Konfig + Guidance nutzbar)
STANDARD_DISABLED_AGENT_IDS = frozenset({"custom_agent_1", "custom_agent_2"})


class AgentWorkflowConfig(Base):
    __tablename__ = "agent_workflow_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_standard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Vollständige Agent-Profile (display_name, role, properties.guidance, …)
    agents: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    edges: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # max_iterations, sandbox_timeout_seconds, notes, description
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Welche optionalen (+fixen) Agenten in der Pipeline aktiv sind
    enabled_agents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
