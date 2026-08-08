"""API für Agent-Workflow-Übersicht und interaktiven Meta-Coach-Chat."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import agent_workflow_store as workflow_store
from app.services import meta_coach_chat

router = APIRouter(prefix="/api/v1/meta-coach", tags=["meta-coach"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)
    log_names: list[str] | None = Field(
        None, description="Optionale Log-Dateinamen, die bevorzugt analysiert werden sollen."
    )
    apply_actions: bool = Field(True, description="Ob vorgeschlagene Änderungen sofort angewendet werden.")


class ChatResponse(BaseModel):
    reply: str
    actions: list[dict[str, Any]] = []
    applied_changes: list[dict[str, Any]] = []
    referenced_logs: list[Any] = []
    llm_configured: bool = False
    workflow: dict[str, Any] | None = None


class WorkflowUpdateRequest(BaseModel):
    max_iterations: int | None = Field(None, ge=1, le=50)
    sandbox_timeout_seconds: int | None = Field(None, ge=5, le=300)
    description: str | None = None
    notes: str | None = None


class AgentPropertyUpdate(BaseModel):
    property: str = Field(..., min_length=1)
    value: Any


@router.get("/workflow")
def get_workflow() -> dict[str, Any]:
    return workflow_store.workflow_overview()


@router.patch("/workflow")
def patch_workflow(body: WorkflowUpdateRequest) -> dict[str, Any]:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Keine Felder zum Aktualisieren.")
    return {"config": workflow_store.update_workflow_keys(updates)}


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: str, body: AgentPropertyUpdate) -> dict[str, Any]:
    try:
        agent = workflow_store.update_agent_property(agent_id, body.property, body.value)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"agent_id": agent_id, "agent": agent}


@router.get("/logs")
def list_logs(include_processed: bool = True, limit: int = 80) -> list[dict[str, Any]]:
    return meta_coach_chat.list_log_files(include_processed=include_processed, limit=limit)


@router.get("/logs/{name}")
def get_log(name: str) -> dict[str, Any]:
    try:
        return meta_coach_chat.read_log_file(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
def coach_chat(body: ChatRequest) -> ChatResponse:
    try:
        result = meta_coach_chat.chat(
            [m.model_dump() for m in body.messages],
            log_names=body.log_names,
            apply_actions=body.apply_actions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Meta-Coach-Call fehlgeschlagen: {exc}") from exc
    return ChatResponse(**result)
