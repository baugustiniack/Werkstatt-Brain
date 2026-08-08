"""Inventory-, Upload- & Crawler-Endpunkte (SPEC Kap. 2.3, 5.3).

Enthält:
    - `GET/POST/PATCH /api/v1/inventory/items`     – unstrukturierte Asset-Bibliothek (Dateien + manuelle Einträge)
    - `POST /api/v1/inventory/items/{id}/process`  – KI-Strukturierung eines einzelnen Eintrags anstoßen
    - `POST /api/v1/inventory/process-pending`     – Batch-KI-Strukturierung ausstehender Einträge
    - `GET /api/v1/inventory/items/{id}/file`      – Rohdatei eines Eintrags (Thumbnails)
    - `POST /api/v1/inventory/upload`      – manueller Datei-Upload inkl. Vision-Pipeline-Trigger
    - `POST /api/v1/inventory/from-concept` – Fallback: Konzept-Foto manuell in Inventar (sonst auto beim CAD-Run, Tag „KI-Generiert“)
    - `POST /api/v1/inventory/migrate-cad` – 1-Klick-Migration eines validierten Sandbox-Modells
    - `POST /api/v1/crawler/scan`          – manueller Anstoß des lokalen Asset-Crawlers (Server-Pfad)

Alle DB-/Sandbox-/Vision-Operationen sind synchron implementiert (SQLAlchemy-
Sync-Session, Docker-SDK, Anthropic-SDK). Routen ohne echte I/O-Awaits werden
daher bewusst als *nicht-async* `def` deklariert, damit FastAPI sie korrekt in
einem Threadpool ausführt statt den Event-Loop zu blockieren; wo tatsächlich
asynchron auf Netzwerk-I/O gewartet wird (Datei-Upload), wird `async def` in
Kombination mit `run_in_threadpool` für die blockierenden Teile verwendet.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import CAD_SNIPPETS_COLLECTION, get_qdrant_client, random_placeholder_vector
from app.models.project_cad import ProjectCad
from app.models.stock_material import StockMaterial
from app.models.tool import Tool, ToolStatus
from app.models.unprocessed_asset import AssetFileType, AssetSource, AssetStatus, UnprocessedAsset
from app.services import crawler, vision_ingest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["inventory"])


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/inventory/upload
# ─────────────────────────────────────────────────────────────────────────────


class AssetUploadResponse(BaseModel):
    asset_id: str
    file_path: str
    file_type: str
    status: str
    duplicate: bool = False
    vision_result: dict[str, Any] | None = None
    created_record: dict[str, Any] | None = None


def _persist_upload_and_ingest(filename: str, content: bytes, title: str | None) -> AssetUploadResponse:
    upload_dir = Path(settings.uploads_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex[:12]}_{Path(filename).name}"
    dest_path = upload_dir / safe_name
    dest_path.write_bytes(content)

    asset_type = crawler.classify_file_type(dest_path)
    file_hash = crawler.compute_file_hash(dest_path)

    db = SessionLocal()
    try:
        existing = db.query(UnprocessedAsset).filter(UnprocessedAsset.file_hash == file_hash).one_or_none()
        if existing is not None:
            dest_path.unlink(missing_ok=True)  # Duplikat – frisch geschriebene Kopie wieder entfernen
            return AssetUploadResponse(
                asset_id=str(existing.id),
                file_path=existing.file_path,
                file_type=existing.file_type.value,
                status=existing.status.value,
                duplicate=True,
                vision_result=existing.vision_result,
            )

        asset = UnprocessedAsset(
            file_path=str(dest_path),
            file_hash=file_hash,
            file_type=asset_type,
            source=AssetSource.UPLOAD,
            title=title or filename,
            status=AssetStatus.PENDING,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

        ingestion = vision_ingest.ingest_asset(db, asset)

        return AssetUploadResponse(
            asset_id=str(asset.id),
            file_path=asset.file_path,
            file_type=asset.file_type.value,
            status=ingestion.get("status", asset.status.value),
            vision_result=asset.vision_result,
            created_record=ingestion.get("created_record"),
        )
    finally:
        db.close()


@router.post("/inventory/upload", response_model=AssetUploadResponse)
async def upload_asset(file: UploadFile = File(...), title: str | None = Form(None)) -> AssetUploadResponse:
    """Nimmt eine beliebige Datei (Bild/CAD/PDF) entgegen, registriert sie in
    der unstrukturierten Asset-Bibliothek (`unprocessed_assets`) und triggert
    für Bilder sofort die KI-Vision-Pipeline (SPEC Kap. 2.3.2, 5.3)."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Leere Datei hochgeladen.")

    return await run_in_threadpool(_persist_upload_and_ingest, file.filename or "upload.bin", content, title)


class ConceptToInventoryRequest(BaseModel):
    session_id: str = Field(..., description="CAD-Session mit generiertem Konzept-Foto")
    conversation_id: str | None = Field(None, description="Chat-Unterhaltung zur Verknüpfung")
    title: str | None = Field(None, description="Anzeigename in der Inventar-DB")
    auto_process: bool = Field(True, description="Sofort per OpenAI Vision beschreiben")


class ConceptToInventoryResponse(BaseModel):
    asset_id: str
    title: str | None
    status: str
    inventory_file_url: str
    conversation_artifact_url: str | None = None
    duplicate: bool = False


@router.post("/inventory/from-concept", response_model=ConceptToInventoryResponse)
def save_concept_to_inventory(body: ConceptToInventoryRequest) -> ConceptToInventoryResponse:
    """Manueller Fallback: Konzept-Foto in Inventar speichern (normalerweise
    automatisch beim CAD-Workflow mit Tag „KI-Generiert“)."""
    from app.services.concept_inventory import save_concept_to_inventory as persist_concept

    db = SessionLocal()
    try:
        result = persist_concept(
            db,
            session_id=body.session_id,
            conversation_id=body.conversation_id,
            title=body.title,
            auto_process=body.auto_process,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Kein Konzept-Foto für diese Session gefunden.")
        return ConceptToInventoryResponse(
            asset_id=result.asset_id,
            title=result.title,
            status=result.status,
            inventory_file_url=result.inventory_file_url,
            conversation_artifact_url=result.conversation_artifact_url,
            duplicate=result.duplicate,
        )
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/inventory/migrate-cad
# ─────────────────────────────────────────────────────────────────────────────


class CadMigrateRequest(BaseModel):
    session_id: str = Field(..., description="Session-ID eines abgeschlossenen /cad/generate-Workflows")
    project_name: str | None = Field(None, description="Optionaler Projektname; sonst automatisch generiert")
    human_rating: int | None = Field(None, ge=1, le=5, description="Optionale User-Bewertung (1-5)")
    part_index: int | None = Field(
        None,
        description=(
            "Index innerhalb `completed_parts` (Mehrteil-Ausarbeitung); ohne Angabe wird das zuletzt "
            "abgeschlossene Teil migriert."
        ),
    )


class CadMigrateResponse(BaseModel):
    project_id: str
    project_name: str
    step_file_path: str | None
    stl_file_path: str | None
    qdrant_indexed: bool


def _index_cad_snippet(project: ProjectCad, requirements_contract: dict[str, Any]) -> bool:
    try:
        client = get_qdrant_client()
        client.upsert(
            collection_name=CAD_SNIPPETS_COLLECTION,
            points=[
                {
                    "id": str(uuid.uuid4()),
                    "vector": random_placeholder_vector(),
                    "payload": {
                        "function_name": project.project_name,
                        "category": ",".join(requirements_contract.get("manufacturing_features", []))
                        or "generic",
                        "code_body": project.generated_code,
                        "python_dependencies": ["build123d"],
                        "rating": project.human_rating,
                        "project_id": str(project.id),
                    },
                }
            ],
        )
        return True
    except Exception as exc:  # noqa: BLE001 - Qdrant-Ausfall darf die Migration nicht blockieren
        logger.warning("Konnte CAD-Snippet nicht in Qdrant indizieren (project_id=%s): %s", project.id, exc)
        return False


def _migrate_cad_sync(request: CadMigrateRequest) -> CadMigrateResponse:
    from agents.graph import WORKFLOW

    config = {"configurable": {"thread_id": request.session_id}}
    snapshot = WORKFLOW.get_state(config)
    values = snapshot.values if snapshot else {}

    if not values:
        raise HTTPException(status_code=404, detail=f"Keine Session '{request.session_id}' gefunden.")

    # Der Supervisor setzt sandbox_result nach jedem abgeschlossenen Teil
    # zurück (Mehrteil-Ausarbeitung) – primär completed_parts auswerten,
    # mit Fallback auf den Top-Level-State für einfache Single-Part-Läufe.
    completed_parts: list[dict[str, Any]] = values.get("completed_parts") or []
    requirements_contract = values.get("requirements_contract") or {}
    generated_code: str | None
    sandbox_result: dict[str, Any]
    part_name: str | None = None

    if completed_parts:
        index = request.part_index if request.part_index is not None else len(completed_parts) - 1
        if not (0 <= index < len(completed_parts)):
            raise HTTPException(status_code=404, detail=f"Kein Teil mit Index {index} vorhanden.")
        part = completed_parts[index]
        sandbox_result = part.get("sandbox_result") or {}
        generated_code = part.get("generated_code")
        requirements_contract = part.get("part_contract") or requirements_contract
        part_name = part.get("name")
    else:
        sandbox_result = values.get("sandbox_result") or {}
        generated_code = values.get("generated_code")

    if sandbox_result.get("status") != "SUCCESS":
        raise HTTPException(
            status_code=400,
            detail="Nur erfolgreich validierte Sandbox-Ergebnisse können in die Inventar-DB migriert werden.",
        )

    export_paths: list[str] = sandbox_result.get("export_paths") or []
    step_path = next((p for p in export_paths if p.endswith(".step")), None)
    stl_path = next((p for p in export_paths if p.endswith(".stl")), None)
    project_name = request.project_name or part_name or f"CAD-Projekt {request.session_id[:8]}"

    db = SessionLocal()
    try:
        project = ProjectCad(
            project_name=project_name,
            raw_prompt=values.get("user_prompt"),
            requirements_contract=requirements_contract,
            generated_code=generated_code,
            step_file_path=step_path,
            stl_file_path=stl_path,
            human_rating=request.human_rating,
        )
        db.add(project)
        db.commit()
        db.refresh(project)

        qdrant_indexed = _index_cad_snippet(project, requirements_contract)

        return CadMigrateResponse(
            project_id=str(project.id),
            project_name=project.project_name,
            step_file_path=project.step_file_path,
            stl_file_path=project.stl_file_path,
            qdrant_indexed=qdrant_indexed,
        )
    finally:
        db.close()


@router.post("/inventory/migrate-cad", response_model=CadMigrateResponse)
def migrate_cad(request: CadMigrateRequest) -> CadMigrateResponse:
    """1-Klick-Migration: überführt ein erfolgreich validiertes Sandbox-Ergebnis
    fest in `projects_cad` (inkl. .step/.stl-Pfaden) und indiziert den
    Code-Snippet in Qdrant `cad_snippets` (SPEC Kap. 2.3.3)."""
    return _migrate_cad_sync(request)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/crawler/scan
# ─────────────────────────────────────────────────────────────────────────────


class CrawlerScanRequest(BaseModel):
    paths: list[str] | None = Field(
        None, description="Optionale Liste zu scannender Pfade; sonst CRAWLER_SCAN_PATHS aus der Konfiguration"
    )


class CrawlerScanResponse(BaseModel):
    scanned_paths: list[str]
    files_seen: int
    new_assets: int
    duplicates_skipped: int
    errors: list[str]


@router.post("/crawler/scan", response_model=CrawlerScanResponse)
def trigger_crawler_scan(request: CrawlerScanRequest | None = None) -> CrawlerScanResponse:
    """Stößt einen manuellen Durchlauf des lokalen PC-Asset-Crawlers an
    (SPEC Kap. 2.3.1, 5.3)."""
    result = crawler.scan_paths(request.paths if request else None)
    return CrawlerScanResponse(**asdict(result))


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/crawler/queue – Warteschlangen-Ansicht für das Dashboard
# ─────────────────────────────────────────────────────────────────────────────


class UnprocessedAssetResponse(BaseModel):
    id: str
    file_path: str
    file_hash: str
    file_type: str
    status: str
    vision_result: dict[str, Any] | None
    error_message: str | None
    discovered_at: str
    processed_at: str | None


class CrawlerQueueResponse(BaseModel):
    items: list[UnprocessedAssetResponse]
    total: int
    pending: int


@router.get("/crawler/queue", response_model=CrawlerQueueResponse)
def get_crawler_queue(limit: int = 100) -> CrawlerQueueResponse:
    """Listet die Crawler-/Upload-Warteschlange `unprocessed_assets` für die
    'PC-Crawler Control'-Ansicht im Dashboard (SPEC Kap. 5.2, Panel 4)."""
    db = SessionLocal()
    try:
        assets = (
            db.query(UnprocessedAsset).order_by(UnprocessedAsset.discovered_at.desc()).limit(limit).all()
        )
        pending_count = db.query(UnprocessedAsset).filter(UnprocessedAsset.status == AssetStatus.PENDING).count()
        total_count = db.query(UnprocessedAsset).count()
        return CrawlerQueueResponse(
            items=[
                UnprocessedAssetResponse(
                    id=str(a.id),
                    file_path=a.file_path,
                    file_hash=a.file_hash,
                    file_type=a.file_type.value,
                    status=a.status.value,
                    vision_result=a.vision_result,
                    error_message=a.error_message,
                    discovered_at=a.discovered_at.isoformat(),
                    processed_at=a.processed_at.isoformat() if a.processed_at else None,
                )
                for a in assets
            ],
            total=total_count,
            pending=pending_count,
        )
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST/PATCH /api/v1/inventory/items – unstrukturierte Asset-Bibliothek
# (Nutzer-Feedback: einfache, durchsuchbare Ablage für Dateien UND manuelle
# Einträge; KI-Strukturierung erfolgt nachträglich, auf Wunsch oder im
# Hintergrund, statt beim Anlegen erzwungen zu werden – SPEC Kap. 2.3, 5.2/5.3)
# ─────────────────────────────────────────────────────────────────────────────


class InventoryItemResponse(BaseModel):
    id: str
    title: str | None
    file_name: str | None
    file_path: str | None
    file_type: str
    source: str
    status: str
    tags: list[str]
    vision_result: dict[str, Any] | None
    notes: str | None
    error_message: str | None
    discovered_at: str
    processed_at: str | None


def _asset_to_item_response(asset: UnprocessedAsset) -> InventoryItemResponse:
    return InventoryItemResponse(
        id=str(asset.id),
        title=asset.title,
        file_name=Path(asset.file_path).name if asset.file_path else None,
        file_path=asset.file_path,
        file_type=asset.file_type.value,
        source=asset.source.value,
        status=asset.status.value,
        tags=asset.tags or [],
        vision_result=asset.vision_result,
        notes=asset.notes,
        error_message=asset.error_message,
        discovered_at=asset.discovered_at.isoformat(),
        processed_at=asset.processed_at.isoformat() if asset.processed_at else None,
    )


class InventoryItemListResponse(BaseModel):
    items: list[InventoryItemResponse]
    total: int
    pending: int


@router.get("/inventory/items", response_model=InventoryItemListResponse)
def list_inventory_items(
    search: str | None = None,
    status: str | None = None,
    file_type: str | None = None,
    source: str | None = None,
    limit: int = 200,
) -> InventoryItemListResponse:
    """Durchsuchbare/filterbare Liste aller Inventar-Einträge (Dateien +
    manuelle Einträge) – ersetzt die reine Fräser-/Materialien-Sicht als
    primäre Inventory-UI (SPEC Kap. 5.2 Panel 4)."""
    db = SessionLocal()
    try:
        query = db.query(UnprocessedAsset)
        if search:
            like = f"%{search}%"
            from sqlalchemy import String, cast

            query = query.filter(
                (UnprocessedAsset.title.ilike(like))
                | (UnprocessedAsset.notes.ilike(like))
                | (UnprocessedAsset.file_path.ilike(like))
                | (cast(UnprocessedAsset.vision_result, String).ilike(like))
            )
        if status:
            try:
                query = query.filter(UnprocessedAsset.status == AssetStatus(status))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Ungültiger status-Filter '{status}'.") from exc
        if file_type:
            try:
                query = query.filter(UnprocessedAsset.file_type == AssetFileType(file_type))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Ungültiger file_type-Filter '{file_type}'.") from exc
        if source:
            try:
                query = query.filter(UnprocessedAsset.source == AssetSource(source))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Ungültiger source-Filter '{source}'.") from exc

        items = query.order_by(UnprocessedAsset.discovered_at.desc()).limit(limit).all()
        # Einmalige Korrektur bekannter Fehlklassifizierungen (z.B. Konzept→material)
        for asset in items:
            try:
                vision_ingest.repair_misclassified_asset(db, asset)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Klassifikations-Repair fehlgeschlagen für %s: %s", asset.id, exc)
            db.refresh(asset)
        pending_count = db.query(UnprocessedAsset).filter(UnprocessedAsset.status == AssetStatus.PENDING).count()
        total_count = db.query(UnprocessedAsset).count()
        return InventoryItemListResponse(
            items=[_asset_to_item_response(a) for a in items], total=total_count, pending=pending_count
        )
    finally:
        db.close()


class ManualEntryCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    auto_process: bool = Field(True, description="Sofort per KI/Heuristik strukturieren, statt nur anzulegen.")


@router.post("/inventory/items", response_model=InventoryItemResponse)
def create_manual_entry(request: ManualEntryCreateRequest) -> InventoryItemResponse:
    """Legt einen manuellen Inventar-Eintrag ohne Datei an (Nutzer-Feedback:
    unstrukturierte Einträge sollen jederzeit manuell möglich sein)."""
    db = SessionLocal()
    try:
        asset = UnprocessedAsset(
            title=request.title,
            notes=request.notes,
            tags=request.tags,
            file_type=AssetFileType.MANUAL,
            source=AssetSource.MANUAL,
            status=AssetStatus.PENDING,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

        if request.auto_process:
            vision_ingest.ingest_asset(db, asset)
            db.refresh(asset)

        return _asset_to_item_response(asset)
    finally:
        db.close()


class InventoryItemUpdateRequest(BaseModel):
    title: str | None = None
    notes: str | None = None
    tags: list[str] | None = None


@router.patch("/inventory/items/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(item_id: str, request: InventoryItemUpdateRequest) -> InventoryItemResponse:
    """Manuelle Bearbeitung/Strukturierung eines Eintrags (Titel/Notiz/Tags) –
    jederzeit statt/zusätzlich zur KI-Strukturierung möglich."""
    db = SessionLocal()
    try:
        asset = db.get(UnprocessedAsset, uuid.UUID(item_id))
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Eintrag '{item_id}' nicht gefunden.")

        if request.title is not None:
            asset.title = request.title
        if request.notes is not None:
            asset.notes = request.notes
        if request.tags is not None:
            asset.tags = request.tags

        db.commit()
        db.refresh(asset)
        return _asset_to_item_response(asset)
    finally:
        db.close()


@router.post("/inventory/items/{item_id}/process", response_model=InventoryItemResponse)
def process_inventory_item(item_id: str) -> InventoryItemResponse:
    """Stößt die KI-Strukturierung (Vision/Text-Ingestion) für genau einen
    Eintrag manuell an/erneut an."""
    db = SessionLocal()
    try:
        asset = db.get(UnprocessedAsset, uuid.UUID(item_id))
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Eintrag '{item_id}' nicht gefunden.")

        vision_ingest.ingest_asset(db, asset)
        db.refresh(asset)
        return _asset_to_item_response(asset)
    finally:
        db.close()


class ProcessPendingResponse(BaseModel):
    processed: int
    indexed: int
    failed: int
    skipped: int = 0


@router.post("/inventory/process-pending", response_model=ProcessPendingResponse)
def process_pending_items(limit: int = 50, include_stubs: bool = True) -> ProcessPendingResponse:
    """Scannt neue und stub-/heuristik-beschriebene Einträge per KI.

    Standard: pending/failed sowie Einträge mit Heuristik-/Stub-Beschreibung
    (z. B. alte PNGs ohne Vision-Key oder „Rohe CAD-Datei…“).
    """
    db = SessionLocal()
    try:
        candidates = (
            db.query(UnprocessedAsset)
            .order_by(UnprocessedAsset.discovered_at.asc())
            .limit(max(limit * 4, 100))
            .all()
        )
        to_process = [
            a
            for a in candidates
            if a.status == AssetStatus.PENDING
            or a.status == AssetStatus.FAILED
            or (include_stubs and vision_ingest.asset_needs_ai_rescan(a))
        ][:limit]

        indexed = 0
        failed = 0
        for asset in to_process:
            result = vision_ingest.ingest_asset(db, asset)
            if result.get("status") == AssetStatus.INDEXED.value:
                indexed += 1
            else:
                failed += 1
        return ProcessPendingResponse(
            processed=len(to_process),
            indexed=indexed,
            failed=failed,
            skipped=max(0, len(candidates) - len(to_process)),
        )
    finally:
        db.close()


@router.get("/inventory/items/{item_id}/file")
def get_inventory_item_file(item_id: str, inline: bool = True) -> FileResponse:
    """Liefert die Rohdatei eines Eintrags (Thumbnails / PDF-/STL-Voransicht).

    `inline=true` (Default) setzt Content-Disposition auf inline, damit Browser
    PDF/Bilder im iframe bzw. Viewer anzeigen statt sofort herunterzuladen.
    """
    db = SessionLocal()
    try:
        asset = db.get(UnprocessedAsset, uuid.UUID(item_id))
        if asset is None or not asset.file_path:
            raise HTTPException(status_code=404, detail="Kein Anhang für diesen Eintrag vorhanden.")

        file_path = Path(asset.file_path)
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Datei existiert nicht (mehr) auf dem Server.")

        suffix = file_path.suffix.lower()
        media_type = mimetypes.guess_type(file_path.name)[0]
        if not media_type:
            media_type = {
                ".stl": "model/stl",
                ".step": "model/step",
                ".stp": "model/step",
                ".pdf": "application/pdf",
            }.get(suffix, "application/octet-stream")

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
            content_disposition_type="inline" if inline else "attachment",
        )
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST /api/v1/inventory/tools – Werkzeug-Verwaltung (SPEC Kap. 5.2, 5.3)
# ─────────────────────────────────────────────────────────────────────────────


class ToolResponse(BaseModel):
    id: str
    name: str
    diameter_mm: float
    flute_length_mm: float | None
    max_rpm: int | None
    feed_rate_mm_min: float | None
    status: str


class ToolUpsertRequest(BaseModel):
    id: str | None = Field(None, description="Vorhandene Tool-ID für ein Update; leer für Neuanlage")
    name: str = Field(..., min_length=1)
    diameter_mm: float = Field(..., gt=0)
    flute_length_mm: float | None = None
    max_rpm: int | None = None
    feed_rate_mm_min: float | None = None
    status: str = Field("neu", description="neu | verschlissen | abgebrochen")


def _tool_to_response(tool: Tool) -> ToolResponse:
    return ToolResponse(
        id=str(tool.id),
        name=tool.name,
        diameter_mm=float(tool.diameter_mm),
        flute_length_mm=float(tool.flute_length_mm) if tool.flute_length_mm is not None else None,
        max_rpm=tool.max_rpm,
        feed_rate_mm_min=float(tool.feed_rate_mm_min) if tool.feed_rate_mm_min is not None else None,
        status=tool.status.value,
    )


@router.get("/inventory/tools", response_model=list[ToolResponse])
def list_tools() -> list[ToolResponse]:
    """Liest die Werkzeug-Datenbank (`tools`) für das Command-Center-
    Quickselect & das Inventar-Panel (SPEC Kap. 5.2, 5.3)."""
    db = SessionLocal()
    try:
        tools = db.query(Tool).order_by(Tool.diameter_mm).all()
        return [_tool_to_response(t) for t in tools]
    finally:
        db.close()


@router.post("/inventory/tools", response_model=ToolResponse)
def upsert_tool(request: ToolUpsertRequest) -> ToolResponse:
    """Legt ein Werkzeug neu an oder aktualisiert ein vorhandenes (per `id`)."""
    try:
        status = ToolStatus(request.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Ungültiger Status '{request.status}' (erwartet: neu|verschlissen|abgebrochen)."
        ) from exc

    db = SessionLocal()
    try:
        tool: Tool | None = None
        if request.id:
            tool = db.get(Tool, uuid.UUID(request.id))
            if tool is None:
                raise HTTPException(status_code=404, detail=f"Werkzeug '{request.id}' nicht gefunden.")
        else:
            tool = Tool(id=uuid.uuid4())
            db.add(tool)

        tool.name = request.name
        tool.diameter_mm = Decimal(str(request.diameter_mm))
        tool.flute_length_mm = Decimal(str(request.flute_length_mm)) if request.flute_length_mm is not None else None
        tool.max_rpm = request.max_rpm
        tool.feed_rate_mm_min = (
            Decimal(str(request.feed_rate_mm_min)) if request.feed_rate_mm_min is not None else None
        )
        tool.status = status

        db.commit()
        db.refresh(tool)
        return _tool_to_response(tool)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# GET/POST /api/v1/inventory/materials – Materiallager-Verwaltung
# ─────────────────────────────────────────────────────────────────────────────


class StockMaterialResponse(BaseModel):
    id: str
    material_type: str
    dimensions_xyz_mm: dict[str, Any]
    grain_direction: str | None
    notes: str | None


class StockMaterialUpsertRequest(BaseModel):
    id: str | None = Field(None, description="Vorhandene Material-ID für ein Update; leer für Neuanlage")
    material_type: str = Field(..., min_length=1)
    dimensions_xyz_mm: dict[str, float] = Field(..., description='{"x": ..., "y": ..., "z": ...} in mm')
    grain_direction: str | None = None
    notes: str | None = None


def _stock_to_response(material: StockMaterial) -> StockMaterialResponse:
    return StockMaterialResponse(
        id=str(material.id),
        material_type=material.material_type,
        dimensions_xyz_mm=material.dimensions_xyz_mm,
        grain_direction=material.grain_direction,
        notes=material.notes,
    )


@router.get("/inventory/materials", response_model=list[StockMaterialResponse])
def list_materials() -> list[StockMaterialResponse]:
    """Liest das Materiallager (`stock_materials`) für das Command-Center-
    Quickselect & das Inventar-Panel (SPEC Kap. 5.2, 5.3)."""
    db = SessionLocal()
    try:
        materials = db.query(StockMaterial).order_by(StockMaterial.material_type).all()
        return [_stock_to_response(m) for m in materials]
    finally:
        db.close()


@router.post("/inventory/materials", response_model=StockMaterialResponse)
def upsert_material(request: StockMaterialUpsertRequest) -> StockMaterialResponse:
    """Legt einen Materialbestand neu an oder aktualisiert einen vorhandenen (per `id`)."""
    db = SessionLocal()
    try:
        material: StockMaterial | None = None
        if request.id:
            material = db.get(StockMaterial, uuid.UUID(request.id))
            if material is None:
                raise HTTPException(status_code=404, detail=f"Material '{request.id}' nicht gefunden.")
        else:
            material = StockMaterial(id=uuid.uuid4())
            db.add(material)

        material.material_type = request.material_type
        material.dimensions_xyz_mm = request.dimensions_xyz_mm
        material.grain_direction = request.grain_direction
        material.notes = request.notes

        db.commit()
        db.refresh(material)
        return _stock_to_response(material)
    finally:
        db.close()
