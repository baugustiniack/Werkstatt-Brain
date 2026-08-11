"""CRUD & Aktivierung für Agent-Workflow-Konfigurationen in PostgreSQL."""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.db.postgres import SessionLocal
from app.models.agent_workflow_config import (
    FIXED_AGENT_IDS,
    STANDARD_DISABLED_AGENT_IDS,
    STANDARD_WORKFLOW_NAME,
    AgentWorkflowConfig,
)
from app.services import agent_workflow_store as defaults

logger = logging.getLogger(__name__)


def _all_default_agent_ids() -> list[str]:
    return list(defaults.DEFAULT_AGENTS.keys())


def _standard_enabled_agents() -> list[str]:
    return [a for a in _all_default_agent_ids() if a not in STANDARD_DISABLED_AGENT_IDS]


def _normalize_enabled(enabled: list[str] | None) -> list[str]:
    known = set(_all_default_agent_ids())
    chosen = [a for a in (enabled or []) if a in known]
    for fixed in FIXED_AGENT_IDS:
        if fixed not in chosen:
            chosen.insert(0, fixed)
    # stabile Reihenfolge analog DEFAULT_AGENTS
    order = _all_default_agent_ids()
    chosen_set = set(chosen)
    return [a for a in order if a in chosen_set]


def _standard_payload() -> dict[str, Any]:
    return {
        "name": STANDARD_WORKFLOW_NAME,
        "description": (
            "Fest hinterlegter Standard-Workflow. Kann jederzeit wieder aktiviert werden. "
            "Nur der Supervisor ist immer Bestandteil; Leer-Agenten sind hier deaktiviert."
        ),
        "is_standard": True,
        "agents": deepcopy(defaults.DEFAULT_AGENTS),
        "edges": deepcopy(defaults.DEFAULT_EDGES),
        "config": deepcopy(defaults.DEFAULT_WORKFLOW),
        "enabled_agents": _standard_enabled_agents(),
    }


def _row_to_dict(row: AgentWorkflowConfig) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "is_standard": bool(row.is_standard),
        "is_active": bool(row.is_active),
        "agents": row.agents or {},
        "edges": row.edges or [],
        "config": row.config or {},
        "enabled_agents": _normalize_enabled(list(row.enabled_agents or [])),
        "fixed_agents": sorted(FIXED_AGENT_IDS),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_standard_workflow(db: Session | None = None) -> AgentWorkflowConfig:
    """Stellt sicher, dass der Standard-Workflow existiert und ggf. aktiv ist."""
    own = db is None
    session = db or SessionLocal()
    try:
        standard = (
            session.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_standard.is_(True))
            .one_or_none()
        )
        payload = _standard_payload()
        if standard is None:
            standard = AgentWorkflowConfig(
                id=uuid.uuid4(),
                name=payload["name"],
                description=payload["description"],
                is_standard=True,
                is_active=False,
                agents=payload["agents"],
                edges=payload["edges"],
                config=payload["config"],
                enabled_agents=payload["enabled_agents"],
            )
            session.add(standard)
            session.flush()
            logger.info("Standard-Agent-Workflow in DB angelegt.")
        else:
            # Standard-Definition aus Code aktualisieren (Name bleibt)
            standard.description = payload["description"]
            standard.agents = payload["agents"]
            standard.edges = payload["edges"]
            standard.config = payload["config"]
            standard.enabled_agents = payload["enabled_agents"]
            standard.name = STANDARD_WORKFLOW_NAME

        active = (
            session.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_active.is_(True))
            .one_or_none()
        )
        if active is None:
            standard.is_active = True

        session.commit()
        session.refresh(standard)
        return standard
    finally:
        if own:
            session.close()


def list_configs() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        rows = (
            db.query(AgentWorkflowConfig)
            .order_by(AgentWorkflowConfig.is_standard.desc(), AgentWorkflowConfig.name.asc())
            .all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def get_active_config() -> dict[str, Any]:
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        row = (
            db.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_active.is_(True))
            .one_or_none()
        )
        if row is None:
            row = ensure_standard_workflow(db)
        return _row_to_dict(row)
    finally:
        db.close()


def get_config(config_id: str) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        row = db.get(AgentWorkflowConfig, uuid.UUID(config_id))
        return _row_to_dict(row) if row else None
    except ValueError:
        return None
    finally:
        db.close()


def activate_config(config_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        row = db.get(AgentWorkflowConfig, uuid.UUID(config_id))
        if row is None:
            raise KeyError(f"Workflow-Konfiguration '{config_id}' nicht gefunden.")
        db.query(AgentWorkflowConfig).update({AgentWorkflowConfig.is_active: False})
        row.is_active = True
        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        db.close()


def activate_standard() -> dict[str, Any]:
    db = SessionLocal()
    try:
        standard = ensure_standard_workflow(db)
        db.query(AgentWorkflowConfig).update({AgentWorkflowConfig.is_active: False})
        standard.is_active = True
        # Standard-Inhalt nochmals aus Code syncen
        payload = _standard_payload()
        standard.agents = payload["agents"]
        standard.edges = payload["edges"]
        standard.config = payload["config"]
        standard.enabled_agents = payload["enabled_agents"]
        db.commit()
        db.refresh(standard)
        return _row_to_dict(standard)
    finally:
        db.close()


def save_as_new(
    *,
    name: str,
    description: str | None = None,
    agents: dict[str, Any] | None = None,
    edges: list[Any] | None = None,
    config: dict[str, Any] | None = None,
    enabled_agents: list[str] | None = None,
    activate: bool = False,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name darf nicht leer sein.")
    if name.lower() == STANDARD_WORKFLOW_NAME.lower():
        raise ValueError(f"Name „{STANDARD_WORKFLOW_NAME}“ ist reserviert.")

    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        active = (
            db.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_active.is_(True))
            .one_or_none()
        )
        base_agents = deepcopy(agents if agents is not None else (active.agents if active else defaults.DEFAULT_AGENTS))
        base_edges = deepcopy(edges if edges is not None else (active.edges if active else defaults.DEFAULT_EDGES))
        base_config = deepcopy(config if config is not None else (active.config if active else defaults.DEFAULT_WORKFLOW))
        base_enabled = _normalize_enabled(
            enabled_agents
            if enabled_agents is not None
            else (list(active.enabled_agents) if active else _all_default_agent_ids())
        )

        existing = db.query(AgentWorkflowConfig).filter(AgentWorkflowConfig.name == name).one_or_none()
        if existing and existing.is_standard:
            raise ValueError("Standard-Workflow kann nicht überschrieben werden.")
        if existing:
            existing.description = description
            existing.agents = base_agents
            existing.edges = base_edges
            existing.config = base_config
            existing.enabled_agents = base_enabled
            row = existing
        else:
            row = AgentWorkflowConfig(
                id=uuid.uuid4(),
                name=name,
                description=description,
                is_standard=False,
                is_active=False,
                agents=base_agents,
                edges=base_edges,
                config=base_config,
                enabled_agents=base_enabled,
            )
            db.add(row)

        if activate:
            db.query(AgentWorkflowConfig).update({AgentWorkflowConfig.is_active: False})
            row.is_active = True

        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        db.close()


def update_active(
    *,
    agents: dict[str, Any] | None = None,
    edges: list[Any] | None = None,
    config: dict[str, Any] | None = None,
    enabled_agents: list[str] | None = None,
    description: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Aktualisiert die aktive Konfiguration (nicht den Standard-Inhalt aus dem Code)."""
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        row = (
            db.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_active.is_(True))
            .one_or_none()
        )
        if row is None:
            row = ensure_standard_workflow(db)

        if row.is_standard:
            # Standard nicht mutieren – speichere als Kopie-Hinweis
            raise PermissionError(
                "Der Standard-Workflow ist schreibgeschützt. "
                "Bitte „Als neue Konfiguration speichern“ nutzen oder eine andere aktivieren."
            )

        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Name darf nicht leer sein.")
            if name.lower() == STANDARD_WORKFLOW_NAME.lower():
                raise ValueError(f"Name „{STANDARD_WORKFLOW_NAME}“ ist reserviert.")
            row.name = name
        if description is not None:
            row.description = description
        if agents is not None:
            row.agents = deepcopy(agents)
        if edges is not None:
            row.edges = deepcopy(edges)
        if config is not None:
            merged = deepcopy(row.config or {})
            merged.update(config)
            row.config = merged
        if enabled_agents is not None:
            row.enabled_agents = _normalize_enabled(enabled_agents)

        db.commit()
        db.refresh(row)
        return _row_to_dict(row)
    finally:
        db.close()


def update_active_agent_property(agent_id: str, property_key: str, value: Any) -> dict[str, Any]:
    db = SessionLocal()
    try:
        ensure_standard_workflow(db)
        row = (
            db.query(AgentWorkflowConfig)
            .filter(AgentWorkflowConfig.is_active.is_(True))
            .one_or_none()
        )
        if row is None:
            row = ensure_standard_workflow(db)
        if row.is_standard:
            raise PermissionError(
                "Standard-Workflow ist schreibgeschützt. Speichere zuerst eine eigene Konfiguration."
            )
        agents = deepcopy(row.agents or {})
        if agent_id not in agents:
            raise KeyError(f"Unbekannter Agent: {agent_id}")
        props = agents[agent_id].setdefault("properties", {})
        props[property_key] = value
        row.agents = agents
        db.commit()
        db.refresh(row)
        return agents[agent_id]
    finally:
        db.close()


def delete_config(config_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(AgentWorkflowConfig, uuid.UUID(config_id))
        if row is None:
            raise KeyError("Nicht gefunden.")
        if row.is_standard:
            raise PermissionError("Standard-Workflow kann nicht gelöscht werden.")
        was_active = row.is_active
        db.delete(row)
        db.commit()
        if was_active:
            activate_standard()
    finally:
        db.close()
