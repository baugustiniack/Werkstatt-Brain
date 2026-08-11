"""API für persistente Agent-Workflow-Konfigurationen (User-editierbar)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import agent_workflow_db as wf_db

router = APIRouter(prefix="/api/v1/agent-workflows", tags=["agent-workflows"])


class SaveWorkflowRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    agents: dict[str, Any] | None = None
    edges: list[Any] | None = None
    config: dict[str, Any] | None = None
    enabled_agents: list[str] | None = None
    activate: bool = False


class UpdateActiveRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None
    agents: dict[str, Any] | None = None
    edges: list[Any] | None = None
    config: dict[str, Any] | None = None
    enabled_agents: list[str] | None = None


class AgentPropertyUpdate(BaseModel):
    property: str = Field(..., min_length=1)
    value: Any


@router.get("")
def list_workflows() -> list[dict[str, Any]]:
    return wf_db.list_configs()


@router.get("/active")
def get_active() -> dict[str, Any]:
    return wf_db.get_active_config()


@router.post("")
def save_workflow(body: SaveWorkflowRequest) -> dict[str, Any]:
    try:
        return wf_db.save_as_new(
            name=body.name,
            description=body.description,
            agents=body.agents,
            edges=body.edges,
            config=body.config,
            enabled_agents=body.enabled_agents,
            activate=body.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/standard/activate")
def activate_standard_workflow() -> dict[str, Any]:
    return wf_db.activate_standard()


@router.post("/{config_id}/activate")
def activate_workflow(config_id: str) -> dict[str, Any]:
    try:
        return wf_db.activate_config(config_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/active")
def update_active_workflow(body: UpdateActiveRequest) -> dict[str, Any]:
    try:
        return wf_db.update_active(
            name=body.name,
            description=body.description,
            agents=body.agents,
            edges=body.edges,
            config=body.config,
            enabled_agents=body.enabled_agents,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/active/agents/{agent_id}")
def update_active_agent(agent_id: str, body: AgentPropertyUpdate) -> dict[str, Any]:
    try:
        agent = wf_db.update_active_agent_property(agent_id, body.property, body.value)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"agent_id": agent_id, "agent": agent}


@router.delete("/{config_id}")
def delete_workflow(config_id: str) -> dict[str, str]:
    try:
        wf_db.delete_config(config_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted", "id": config_id}
