"""REST-API für Chat-Unterhaltungen (Gemini-ähnlich: Liste, Wechsel, Artefakte)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.db.postgres import SessionLocal
from app.services import conversation_store as store

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    cad_session_id: str | None = None
    meta: dict[str, Any] | None = None
    created_at: str


class ArtifactOut(BaseModel):
    id: str
    kind: str
    label: str | None
    part_index: int | None
    cad_session_id: str | None
    url: str
    meta: dict[str, Any] | None = None
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[MessageOut]
    artifacts: list[ArtifactOut]


class CreateConversationRequest(BaseModel):
    title: str | None = None


class AddMessageRequest(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1)
    cad_session_id: str | None = None
    meta: dict[str, Any] | None = None


def _artifact_url(artifact_id: uuid.UUID) -> str:
    return f"/api/v1/conversations/artifacts/{artifact_id}/file"


@router.get("", response_model=list[ConversationSummary])
def list_conversations() -> list[ConversationSummary]:
    db = SessionLocal()
    try:
        rows = store.list_conversations(db)
        return [
            ConversationSummary(
                id=str(c.id),
                title=c.title,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
                message_count=len(c.messages) if c.messages is not None else 0,
            )
            for c in rows
        ]
    finally:
        db.close()


@router.post("", response_model=ConversationSummary)
def create_conversation(body: CreateConversationRequest | None = None) -> ConversationSummary:
    db = SessionLocal()
    try:
        conv = store.create_conversation(db, title=(body.title if body else None))
        return ConversationSummary(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            message_count=0,
        )
    finally:
        db.close()


@router.get("/artifacts/{artifact_id}/file")
def download_artifact(artifact_id: uuid.UUID) -> FileResponse:
    db = SessionLocal()
    try:
        from app.models.conversation import ConversationArtifact

        art = db.get(ConversationArtifact, artifact_id)
        if not art:
            raise HTTPException(status_code=404, detail="Artefakt nicht gefunden.")
        path = Path(art.file_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Datei fehlt auf dem Server.")
        media = {
            "concept_image": "image/png",
            "step": "model/step",
            "stl": "model/stl",
            "transcript": "text/plain",
        }.get(art.kind, "application/octet-stream")
        return FileResponse(path=path, media_type=media, filename=path.name)
    finally:
        db.close()


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID) -> ConversationDetail:
    db = SessionLocal()
    try:
        conv = store.get_conversation(db, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden.")
        return ConversationDetail(
            id=str(conv.id),
            title=conv.title,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
            messages=[
                MessageOut(
                    id=str(m.id),
                    role=m.role,
                    content=m.content,
                    cad_session_id=m.cad_session_id,
                    meta=m.meta,
                    created_at=m.created_at.isoformat(),
                )
                for m in conv.messages
            ],
            artifacts=[
                ArtifactOut(
                    id=str(a.id),
                    kind=a.kind,
                    label=a.label or Path(a.file_path).name,
                    part_index=a.part_index,
                    cad_session_id=a.cad_session_id,
                    url=_artifact_url(a.id),
                    meta=a.meta,
                    created_at=a.created_at.isoformat(),
                )
                for a in sorted(conv.artifacts, key=lambda x: x.created_at)
            ],
        )
    finally:
        db.close()


@router.post("/{conversation_id}/messages", response_model=MessageOut)
def add_message(conversation_id: uuid.UUID, body: AddMessageRequest) -> MessageOut:
    db = SessionLocal()
    try:
        if not store.get_conversation(db, conversation_id):
            raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden.")
        msg = store.add_message(
            db,
            conversation_id,
            body.role,
            body.content,
            cad_session_id=body.cad_session_id,
            meta=body.meta,
        )
        return MessageOut(
            id=str(msg.id),
            role=msg.role,
            content=msg.content,
            cad_session_id=msg.cad_session_id,
            meta=msg.meta,
            created_at=msg.created_at.isoformat(),
        )
    finally:
        db.close()


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: uuid.UUID) -> dict[str, str]:
    db = SessionLocal()
    try:
        conv = store.get_conversation(db, conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden.")
        db.delete(conv)
        db.commit()
        return {"status": "deleted", "id": str(conversation_id)}
    finally:
        db.close()