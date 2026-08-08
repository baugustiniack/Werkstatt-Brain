"""Automatische Ablage von Konzept-Fotos in der Inventar-DB."""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.conversation import ConversationArtifact
from app.models.unprocessed_asset import AssetFileType, AssetSource, AssetStatus, UnprocessedAsset
from app.services import conversation_store, crawler, vision_ingest
from app.services.concept_image import concept_image_path

logger = logging.getLogger(__name__)

TAG_AI_GENERATED = "KI-Generiert"
TAG_CONCEPT = "concept_image"


@dataclass
class ConceptInventoryResult:
    asset_id: str
    title: str | None
    status: str
    inventory_file_url: str
    conversation_artifact_url: str | None = None
    duplicate: bool = False


def _resolve_concept_source(
    db: Session,
    session_id: str,
    conversation_id: str | None,
) -> Path | None:
    src = concept_image_path(session_id)
    if src is not None and src.is_file():
        return src

    if not conversation_id:
        return None
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        return None

    art = (
        db.query(ConversationArtifact)
        .filter(
            ConversationArtifact.conversation_id == conv_uuid,
            ConversationArtifact.cad_session_id == session_id,
            ConversationArtifact.kind == "concept_image",
        )
        .first()
    )
    if art and art.file_path and Path(art.file_path).is_file():
        return Path(art.file_path)
    return None


def _ensure_conversation_concept_artifact(
    db: Session,
    conversation_id: str,
    session_id: str,
    src: Path,
) -> str | None:
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        return None
    if not conversation_store.get_conversation(db, conv_uuid):
        return None

    existing = (
        db.query(ConversationArtifact)
        .filter(
            ConversationArtifact.conversation_id == conv_uuid,
            ConversationArtifact.cad_session_id == session_id,
            ConversationArtifact.kind == "concept_image",
        )
        .first()
    )
    if existing:
        return f"/api/v1/conversations/artifacts/{existing.id}/file"

    dest = conversation_store._copy_into_conversation(  # noqa: SLF001
        conv_uuid, src, filename=f"{session_id}_concept.png"
    )
    if not dest:
        return None
    art = ConversationArtifact(
        conversation_id=conv_uuid,
        cad_session_id=session_id,
        kind="concept_image",
        file_path=str(dest),
        label="Konzept-Foto",
    )
    db.add(art)
    db.commit()
    db.refresh(art)
    return f"/api/v1/conversations/artifacts/{art.id}/file"


def _concept_tags(conversation_id: str | None) -> list[str]:
    tags = [TAG_AI_GENERATED, TAG_CONCEPT, "from_chat"]
    if conversation_id:
        tags.append(f"conversation:{conversation_id}")
    return tags


def save_concept_to_inventory(
    db: Session,
    *,
    session_id: str,
    conversation_id: str | None = None,
    title: str | None = None,
    auto_process: bool = True,
) -> ConceptInventoryResult | None:
    """Kopiert ein Konzept-Foto in die Inventar-DB, taggt es als KI-generiert
    und verknüpft es optional mit der Chat-Unterhaltung. Idempotent per file_hash."""
    src = _resolve_concept_source(db, session_id, conversation_id)
    if src is None:
        return None

    upload_dir = Path(settings.uploads_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{uuid.uuid4().hex[:12]}_concept_{Path(session_id).name[:24]}.png"
    dest = upload_dir / dest_name

    file_hash = crawler.compute_file_hash(src)
    resolved_title = (title or f"Konzept {session_id[:8]}").strip()[:255]
    notes_parts = [
        f"Konzept-Foto aus CAD-Session {session_id}.",
        "Automatisch aus dem CAD-Workflow in die Inventar-DB übernommen.",
        f"Tag: {TAG_AI_GENERATED}",
    ]
    if conversation_id:
        notes_parts.append(f"Verknüpfte Unterhaltung: {conversation_id}")
    notes = "\n".join(notes_parts)
    tags = _concept_tags(conversation_id)

    existing = db.query(UnprocessedAsset).filter(UnprocessedAsset.file_hash == file_hash).one_or_none()
    if existing is not None:
        merged_tags = list(dict.fromkeys([*(existing.tags or []), *tags]))
        existing.tags = merged_tags
        if conversation_id and (not existing.notes or "Verknüpfte Unterhaltung" not in (existing.notes or "")):
            existing.notes = ((existing.notes or "") + "\n" + notes).strip()
        if not existing.title:
            existing.title = resolved_title
        db.commit()
        db.refresh(existing)
        artifact_url = None
        if conversation_id:
            artifact_url = _ensure_conversation_concept_artifact(db, conversation_id, session_id, src)
        return ConceptInventoryResult(
            asset_id=str(existing.id),
            title=existing.title,
            status=existing.status.value,
            inventory_file_url=f"/api/v1/inventory/items/{existing.id}/file",
            conversation_artifact_url=artifact_url,
            duplicate=True,
        )

    shutil.copy2(src, dest)
    asset = UnprocessedAsset(
        file_path=str(dest),
        file_hash=file_hash,
        file_type=AssetFileType.IMAGE,
        source=AssetSource.UPLOAD,
        title=resolved_title,
        notes=notes,
        tags=tags,
        status=AssetStatus.PENDING,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    if auto_process:
        try:
            vision_ingest.ingest_asset(db, asset)
            db.refresh(asset)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vision-Ingest für Konzept-Foto fehlgeschlagen: %s", exc)

    artifact_url = None
    if conversation_id:
        artifact_url = _ensure_conversation_concept_artifact(db, conversation_id, session_id, src)

    return ConceptInventoryResult(
        asset_id=str(asset.id),
        title=asset.title,
        status=asset.status.value,
        inventory_file_url=f"/api/v1/inventory/items/{asset.id}/file",
        conversation_artifact_url=artifact_url,
        duplicate=False,
    )


def auto_persist_concept_inventory(
    *,
    session_id: str,
    conversation_id: str | None,
    values: dict[str, Any] | None = None,
) -> ConceptInventoryResult | None:
    """Wird vom CAD-Persistenzpfad aufgerufen, sobald ein Konzept-Foto vorliegt."""
    from app.db.postgres import SessionLocal

    if not concept_image_path(session_id) and not conversation_id:
        return None

    title = None
    if values:
        contract = values.get("requirements_contract") or {}
        if isinstance(contract, dict):
            title = contract.get("project_title") or None

    db = SessionLocal()
    try:
        result = save_concept_to_inventory(
            db,
            session_id=session_id,
            conversation_id=conversation_id,
            title=title,
            auto_process=True,
        )
        if result:
            logger.info(
                "Konzept-Foto in Inventar gespeichert (asset=%s, duplicate=%s, session=%s)",
                result.asset_id,
                result.duplicate,
                session_id,
            )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("Automatische Inventar-Ablage für Konzept fehlgeschlagen: %s", exc)
        return None
    finally:
        db.close()
