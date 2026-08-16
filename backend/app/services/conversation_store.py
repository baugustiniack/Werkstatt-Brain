"""Persistenz für Chat-Unterhaltungen, Nachrichten und CAD-Artefakte."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.conversation import Conversation, ConversationArtifact, ConversationMessage

logger = logging.getLogger(__name__)


def _conv_dir(conversation_id: uuid.UUID | str) -> Path:
    path = Path(settings.conversations_dir) / str(conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_conversation(db: Session, title: str | None = None) -> Conversation:
    conv = Conversation(title=(title or "Neue Unterhaltung")[:255])
    db.add(conv)
    db.commit()
    db.refresh(conv)
    _conv_dir(conv.id)
    return conv


def list_conversations(db: Session, limit: int = 50) -> list[Conversation]:
    from sqlalchemy.orm import joinedload

    return (
        db.query(Conversation)
        .options(joinedload(Conversation.messages))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )


def get_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    from sqlalchemy.orm import joinedload

    return (
        db.query(Conversation)
        .options(
            joinedload(Conversation.messages),
            joinedload(Conversation.artifacts),
        )
        .filter(Conversation.id == conversation_id)
        .first()
    )


def touch_conversation(db: Session, conversation: Conversation) -> None:
    from datetime import datetime, timezone

    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()


def add_message(
    db: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    *,
    cad_session_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ConversationMessage:
    msg = ConversationMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        cad_session_id=cad_session_id,
        meta=meta,
    )
    db.add(msg)
    conv = db.get(Conversation, conversation_id)
    if conv and role == "user" and (conv.title == "Neue Unterhaltung" or not conv.title):
        conv.title = content.strip()[:80] or conv.title
    if conv:
        from datetime import datetime, timezone

        conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(msg)
    return msg


def _copy_into_conversation(
    conversation_id: uuid.UUID,
    src: Path,
    *,
    filename: str,
) -> Path | None:
    if not src.is_file():
        return None
    dest = _conv_dir(conversation_id) / filename
    shutil.copy2(src, dest)
    return dest


def persist_session_artifacts(
    db: Session,
    conversation_id: uuid.UUID,
    cad_session_id: str,
    values: dict[str, Any],
    *,
    message_id: uuid.UUID | None = None,
) -> list[ConversationArtifact]:
    """Kopiert Konzeptfoto + STEP/STL in den Conversation-Ordner.

    Wichtig: Es wird nichts gelöscht oder überschrieben. Jedes neue Konzept-Foto
    wird als eigener Artefakt-Eintrag versioniert abgelegt.
    """
    from datetime import datetime, timezone

    created: list[ConversationArtifact] = []

    existing = (
        db.query(ConversationArtifact)
        .filter(
            ConversationArtifact.conversation_id == conversation_id,
            ConversationArtifact.cad_session_id == cad_session_id,
        )
        .all()
    )
    existing_export_keys = {
        (a.kind, a.part_index)
        for a in existing
        if a.kind in ("step", "stl")
    }

    # Konzeptbild – immer neue Version (nie überschreiben), gleicher Inhalt nur einmal
    from app.services import crawler
    from app.services.concept_image import concept_image_path

    img = concept_image_path(cad_session_id)
    if img and img.is_file():
        file_hash = crawler.compute_file_hash(img)
        prior_concepts = (
            db.query(ConversationArtifact)
            .filter(
                ConversationArtifact.conversation_id == conversation_id,
                ConversationArtifact.kind == "concept_image",
            )
            .all()
        )
        hash_known = any((a.meta or {}).get("file_hash") == file_hash for a in prior_concepts)
        if not hash_known:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            dest = _copy_into_conversation(
                conversation_id,
                img,
                filename=f"{cad_session_id}_concept_{stamp}.png",
            )
            if dest:
                contract = values.get("requirements_contract") if isinstance(values.get("requirements_contract"), dict) else None
                title = (contract or {}).get("project_title") or "Konzept-Foto"
                art = ConversationArtifact(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    cad_session_id=cad_session_id,
                    kind="concept_image",
                    file_path=str(dest),
                    label=str(title)[:255],
                    meta={
                        "ausarbeiten": True,
                        "file_hash": file_hash,
                        "project_title": title,
                        "requirements_contract": contract,
                        "user_prompt": values.get("user_prompt"),
                        "concept_sketch_svg": values.get("concept_sketch_svg"),
                        "vv_requirements": values.get("vv_requirements"),
                        "concept_image_url": values.get("concept_image_url"),
                        "concept_image_urls": values.get("concept_image_urls"),
                        "session_id": cad_session_id,
                    },
                )
                db.add(art)
                created.append(art)

    completed = values.get("completed_parts") or []
    for idx, part in enumerate(completed):
        name = part.get("name") or f"Teil {idx + 1}"
        export_paths = (part.get("sandbox_result") or {}).get("export_paths") or []
        for export in export_paths:
            src = Path(export)
            kind = "step" if src.suffix.lower() == ".step" else "stl" if src.suffix.lower() == ".stl" else None
            if not kind:
                continue
            if (kind, idx) in existing_export_keys:
                continue
            dest = _copy_into_conversation(
                conversation_id,
                src,
                filename=f"{cad_session_id}_part{idx}_{kind}{src.suffix.lower()}",
            )
            if dest:
                art = ConversationArtifact(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    cad_session_id=cad_session_id,
                    kind=kind,
                    file_path=str(dest),
                    label=name,
                    part_index=idx,
                    meta={"session_id": cad_session_id, "part_name": name},
                )
                db.add(art)
                created.append(art)
                existing_export_keys.add((kind, idx))

    # Fallback: top-level sandbox_result
    if not completed:
        sandbox = values.get("sandbox_result") or {}
        for export in sandbox.get("export_paths") or []:
            src = Path(export)
            kind = "step" if src.suffix.lower() == ".step" else "stl" if src.suffix.lower() == ".stl" else None
            if not kind or not src.is_file():
                continue
            if (kind, 0) in existing_export_keys:
                continue
            dest = _copy_into_conversation(
                conversation_id, src, filename=f"{cad_session_id}_{kind}{src.suffix.lower()}"
            )
            if dest:
                art = ConversationArtifact(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    cad_session_id=cad_session_id,
                    kind=kind,
                    file_path=str(dest),
                    label="Export",
                    part_index=0,
                    meta={"session_id": cad_session_id},
                )
                db.add(art)
                created.append(art)
                existing_export_keys.add((kind, 0))

    if created or (img and img.is_file()):
        touch_conversation(db, db.get(Conversation, conversation_id))  # type: ignore[arg-type]
        db.commit()
        for art in created:
            db.refresh(art)
    return created
