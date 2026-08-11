"""Automatische Ablage von KI-generierten 3D-Modellen (STEP/STL) in der Inventar-DB.

Jedes erfolgreich validierte Bauteil aus einem Chat wird als UnprocessedAsset
gespeichert, mit `KI-Generiert` / `generated_3d` getaggt und über
`conversation:{id}` mit der Unterhaltung verknüpft – damit Inventory Manager
und Inventory-KI die Modelle wiederfinden.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.unprocessed_asset import AssetFileType, AssetSource, AssetStatus, UnprocessedAsset
from app.services import crawler, vision_ingest
from app.services.inventory_notes import effective_ai_notes, set_ai_notes

logger = logging.getLogger(__name__)

TAG_AI_GENERATED = "KI-Generiert"
TAG_GENERATED_3D = "generated_3d"
TAG_CAD_MODEL = "cad_model"
TAG_FROM_CHAT = "from_chat"


@dataclass
class CadModelInventoryResult:
    asset_id: str
    title: str | None
    status: str
    kind: str  # step | stl
    part_index: int
    inventory_file_url: str
    duplicate: bool = False


def _model_tags(conversation_id: str | None, *, kind: str, part_index: int) -> list[str]:
    tags = [
        TAG_AI_GENERATED,
        TAG_GENERATED_3D,
        TAG_CAD_MODEL,
        TAG_FROM_CHAT,
        kind,
        f"part:{part_index}",
    ]
    if conversation_id:
        tags.append(f"conversation:{conversation_id}")
    return tags


def _part_dimensions_text(part_contract: dict[str, Any] | None) -> str:
    if not isinstance(part_contract, dict):
        return ""
    geom = part_contract.get("functional_geometry") or {}
    dims = geom.get("dimensions_mm") or {}
    if not isinstance(dims, dict):
        return ""
    x, y, z = dims.get("x"), dims.get("y"), dims.get("z")
    if x is None and y is None and z is None:
        return ""
    return f"Maße (mm): {x}×{y}×{z}."


def _build_notes(
    *,
    session_id: str,
    conversation_id: str | None,
    project_title: str | None,
    part_name: str | None,
    part_index: int,
    kind: str,
    part_contract: dict[str, Any] | None,
) -> str:
    lines = [
        f"KI-generiertes 3D-Modell ({kind.upper()}) aus dem CAD-Chat.",
        f"Session: {session_id}.",
        f"Teil-Index: {part_index}" + (f" („{part_name}“)" if part_name else "") + ".",
    ]
    if project_title:
        lines.append(f"Projekt: {project_title}.")
    dim = _part_dimensions_text(part_contract)
    if dim:
        lines.append(dim)
    mat = None
    if isinstance(part_contract, dict):
        mat = (part_contract.get("material_tool_constraints") or {}).get("material_type")
    if mat:
        lines.append(f"Material (Konzept): {mat}.")
    lines.append(
        "Automatisch aus dem CAD-Workflow in die Inventar-DB übernommen. "
        f"Tags: {TAG_AI_GENERATED}, {TAG_GENERATED_3D}."
    )
    if conversation_id:
        lines.append(f"Verknüpfte Unterhaltung: {conversation_id}")
    return "\n".join(lines)


def _file_type_for_kind(kind: str) -> AssetFileType:
    if kind == "step":
        return AssetFileType.STEP
    if kind == "stl":
        return AssetFileType.STL
    return AssetFileType.OTHER


def save_cad_export_to_inventory(
    db: Session,
    *,
    src: Path,
    kind: str,
    session_id: str,
    conversation_id: str | None = None,
    part_index: int = 0,
    part_name: str | None = None,
    project_title: str | None = None,
    part_contract: dict[str, Any] | None = None,
    auto_process: bool = True,
) -> CadModelInventoryResult | None:
    """Kopiert eine STEP/STL-Datei ins Inventar und taggt sie als KI-generiert."""
    if not src.is_file():
        return None
    kind = kind.lower().lstrip(".")
    if kind not in ("step", "stl"):
        return None

    upload_dir = Path(settings.uploads_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_hash = crawler.compute_file_hash(src)
    title_bits = [
        project_title or "CAD-Modell",
        part_name or f"Teil {part_index + 1}",
        kind.upper(),
    ]
    resolved_title = " – ".join(b for b in title_bits if b)[:255]
    notes = _build_notes(
        session_id=session_id,
        conversation_id=conversation_id,
        project_title=project_title,
        part_name=part_name,
        part_index=part_index,
        kind=kind,
        part_contract=part_contract,
    )
    tags = _model_tags(conversation_id, kind=kind, part_index=part_index)

    existing = db.query(UnprocessedAsset).filter(UnprocessedAsset.file_hash == file_hash).one_or_none()
    if existing is not None:
        merged_tags = list(dict.fromkeys([*(existing.tags or []), *tags]))
        existing.tags = merged_tags
        if conversation_id and "Verknüpfte Unterhaltung" not in (effective_ai_notes(existing) or ""):
            set_ai_notes(existing, ((effective_ai_notes(existing) or "") + "\n" + notes).strip())
        if not existing.title:
            existing.title = resolved_title
        # Sicherstellen, dass KI-Tags bleiben
        if TAG_AI_GENERATED not in (existing.tags or []):
            existing.tags = list(dict.fromkeys([*(existing.tags or []), TAG_AI_GENERATED]))
        db.commit()
        db.refresh(existing)
        return CadModelInventoryResult(
            asset_id=str(existing.id),
            title=existing.title,
            status=existing.status.value,
            kind=kind,
            part_index=part_index,
            inventory_file_url=f"/api/v1/inventory/items/{existing.id}/file",
            duplicate=True,
        )

    dest_name = (
        f"{uuid.uuid4().hex[:12]}_chat_{kind}_p{part_index}_{Path(session_id).name[:16]}"
        f"{src.suffix.lower()}"
    )
    dest = upload_dir / dest_name
    shutil.copy2(src, dest)

    asset = UnprocessedAsset(
        file_path=str(dest),
        file_hash=file_hash,
        file_type=_file_type_for_kind(kind),
        source=AssetSource.UPLOAD,
        title=resolved_title,
        tags=tags,
        status=AssetStatus.PENDING,
    )
    set_ai_notes(asset, notes)
    db.add(asset)
    db.commit()
    db.refresh(asset)

    if auto_process:
        try:
            vision_ingest.ingest_asset(db, asset)
            db.refresh(asset)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ingest für KI-3D-Modell fehlgeschlagen (%s): %s", asset.id, exc)

    return CadModelInventoryResult(
        asset_id=str(asset.id),
        title=asset.title,
        status=asset.status.value,
        kind=kind,
        part_index=part_index,
        inventory_file_url=f"/api/v1/inventory/items/{asset.id}/file",
        duplicate=False,
    )


def persist_completed_parts_to_inventory(
    db: Session,
    *,
    session_id: str,
    conversation_id: str | None,
    values: dict[str, Any],
    auto_process: bool = True,
) -> list[CadModelInventoryResult]:
    """Legt alle STEP/STL aus completed_parts (und ggf. aktuellem sandbox_result) ab."""
    results: list[CadModelInventoryResult] = []
    contract = values.get("requirements_contract") or {}
    project_title = contract.get("project_title") if isinstance(contract, dict) else None
    completed = values.get("completed_parts") or []

    for idx, part in enumerate(completed):
        if not isinstance(part, dict):
            continue
        part_name = part.get("name") or f"Teil {idx + 1}"
        part_contract = part.get("part_contract") if isinstance(part.get("part_contract"), dict) else None
        export_paths = (part.get("sandbox_result") or {}).get("export_paths") or []
        for export in export_paths:
            src = Path(export)
            suffix = src.suffix.lower().lstrip(".")
            if suffix not in ("step", "stl"):
                continue
            saved = save_cad_export_to_inventory(
                db,
                src=src,
                kind=suffix,
                session_id=session_id,
                conversation_id=conversation_id,
                part_index=idx,
                part_name=part_name,
                project_title=project_title,
                part_contract=part_contract,
                auto_process=auto_process,
            )
            if saved:
                results.append(saved)

    # Fallback: noch nicht in completed_parts, aber erfolgreicher Top-Level-Sandbox
    if not completed:
        sandbox = values.get("sandbox_result") or {}
        if isinstance(sandbox, dict) and sandbox.get("status") == "SUCCESS":
            parts = (contract.get("parts") or []) if isinstance(contract, dict) else []
            idx = int(values.get("current_part_index") or 0)
            part_contract = parts[idx] if 0 <= idx < len(parts) else None
            part_name = (part_contract or {}).get("name") if isinstance(part_contract, dict) else None
            for export in sandbox.get("export_paths") or []:
                src = Path(export)
                suffix = src.suffix.lower().lstrip(".")
                if suffix not in ("step", "stl"):
                    continue
                saved = save_cad_export_to_inventory(
                    db,
                    src=src,
                    kind=suffix,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    part_index=idx,
                    part_name=part_name,
                    project_title=project_title,
                    part_contract=part_contract if isinstance(part_contract, dict) else None,
                    auto_process=auto_process,
                )
                if saved:
                    results.append(saved)

    return results


def auto_persist_cad_models_inventory(
    *,
    session_id: str,
    conversation_id: str | None,
    values: dict[str, Any] | None = None,
) -> list[CadModelInventoryResult]:
    """Wird vom CAD-Persistenzpfad aufgerufen, sobald STEP/STL vorliegen."""
    from app.db.postgres import SessionLocal

    if not values:
        return []
    completed = values.get("completed_parts") or []
    sandbox = values.get("sandbox_result") or {}
    has_exports = bool(completed) or (
        isinstance(sandbox, dict)
        and sandbox.get("status") == "SUCCESS"
        and (sandbox.get("export_paths") or [])
    )
    if not has_exports:
        return []

    db = SessionLocal()
    try:
        results = persist_completed_parts_to_inventory(
            db,
            session_id=session_id,
            conversation_id=conversation_id,
            values=values,
            auto_process=True,
        )
        if results:
            logger.info(
                "KI-3D-Modelle in Inventar: %s Datei(en) (session=%s, conversation=%s)",
                len(results),
                session_id,
                conversation_id,
            )
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning("Automatische Inventar-Ablage für 3D-Modelle fehlgeschlagen: %s", exc)
        return []
    finally:
        db.close()
