"""Local PC-Asset Crawler – rekursives Scannen konfigurierbarer Pfade nach
CAD-/Bild-Dateien mit SHA256-Duplikatschutz (SPEC Kap. 2.3.1).

Neue Funde landen mit Status `pending` in `unprocessed_assets` und warten
dort auf die KI-Vision-Ingestion-Pipeline (`vision_ingest.py`) bzw. den
Meta-Coach (`meta_coach.py`).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.postgres import SessionLocal
from app.models.unprocessed_asset import AssetFileType, AssetStatus, UnprocessedAsset

logger = logging.getLogger(__name__)

_EXTENSION_TO_ASSET_TYPE: dict[str, AssetFileType] = {
    ".step": AssetFileType.STEP,
    ".stp": AssetFileType.STEP,
    ".stl": AssetFileType.STL,
    ".f3d": AssetFileType.F3D,
    ".png": AssetFileType.IMAGE,
    ".jpg": AssetFileType.IMAGE,
    ".jpeg": AssetFileType.IMAGE,
    ".webp": AssetFileType.IMAGE,
    ".gif": AssetFileType.IMAGE,
    ".bmp": AssetFileType.IMAGE,
    ".tif": AssetFileType.IMAGE,
    ".tiff": AssetFileType.IMAGE,
    ".pdf": AssetFileType.PDF,
}

_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass
class CrawlerScanResult:
    """Ergebnis eines Crawler-Durchlaufs (SPEC Kap. 2.3.1, 5.3 `/api/v1/crawler/scan`)."""

    scanned_paths: list[str] = field(default_factory=list)
    files_seen: int = 0
    new_assets: int = 0
    duplicates_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def classify_file_type(path: Path) -> AssetFileType:
    """Ordnet eine Datei einem `AssetFileType` zu; unbekannte Endungen → OTHER."""
    mapped = _EXTENSION_TO_ASSET_TYPE.get(path.suffix.lower())
    return mapped if mapped is not None else AssetFileType.OTHER


def compute_file_hash(path: Path) -> str:
    """Berechnet den SHA256-Hash einer Datei (Duplikatschutz, SPEC Kap. 2.1.1)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_candidate_files(root: Path, allowed_extensions: set[str]) -> list[Path]:
    if not root.exists():
        return []
    # Leeres Allowlist-Set = alle Dateien (Upload-ähnliches Verhalten auf dem Server)
    if not allowed_extensions:
        return [candidate for candidate in root.rglob("*") if candidate.is_file()]
    return [
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() in allowed_extensions
    ]


def _register_asset(db: Session, path: Path, file_hash: str, asset_type: AssetFileType) -> bool:
    """Legt einen neuen `unprocessed_assets`-Eintrag an, falls Hash & Pfad noch unbekannt sind.

    Gibt True zurück, wenn ein neuer Eintrag erstellt wurde, sonst False (Duplikat)."""
    existing = db.execute(
        select(UnprocessedAsset).where(
            (UnprocessedAsset.file_hash == file_hash) | (UnprocessedAsset.file_path == str(path))
        )
    ).scalar_one_or_none()

    if existing is not None:
        return False

    db.add(
        UnprocessedAsset(
            file_path=str(path),
            file_hash=file_hash,
            file_type=asset_type,
            status=AssetStatus.PENDING,
            title=path.name,
        )
    )
    return True


def scan_paths(paths: list[str] | None = None) -> CrawlerScanResult:
    """Scannt die konfigurierten (oder übergebenen) Pfade rekursiv nach neuen Assets.

    Wird sowohl vom manuellen Endpunkt `POST /api/v1/crawler/scan` als auch
    potenziell vom Meta-Coach-Scheduler (Nachtbetrieb, Kap. 2.5) aufgerufen.
    """
    scan_targets = paths or settings.crawler_scan_path_list
    allowed_extensions = settings.crawler_file_extension_set
    result = CrawlerScanResult(scanned_paths=scan_targets)

    db = SessionLocal()
    try:
        for raw_path in scan_targets:
            root = Path(raw_path)
            try:
                candidates = _iter_candidate_files(root, allowed_extensions)
            except OSError as exc:
                result.errors.append(f"{raw_path}: {exc}")
                continue

            for candidate in candidates:
                result.files_seen += 1
                asset_type = classify_file_type(candidate)

                try:
                    file_hash = compute_file_hash(candidate)
                except OSError as exc:
                    result.errors.append(f"{candidate}: {exc}")
                    continue

                if _register_asset(db, candidate, file_hash, asset_type):
                    result.new_assets += 1
                else:
                    result.duplicates_skipped += 1

        db.commit()
    except Exception as exc:  # noqa: BLE001 - Crawler darf den Prozess nie hart abbrechen
        db.rollback()
        logger.error("Asset-Crawler fehlgeschlagen: %s", exc)
        result.errors.append(str(exc))
    finally:
        db.close()

    logger.info(
        "Crawler-Scan abgeschlossen: %d Dateien gesehen, %d neu, %d Duplikate, %d Fehler",
        result.files_seen,
        result.new_assets,
        result.duplicates_skipped,
        len(result.errors),
    )
    return result
