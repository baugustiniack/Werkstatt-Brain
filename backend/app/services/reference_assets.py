"""Chat-Referenzbilder: IDs aus dem Prompt + Dateipfade für Vision/Images-API."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ASSET_ID_RE = re.compile(
    r"(?:id=|/items/)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}
_PDF_SUFFIXES = {".pdf"}


def extract_reference_asset_ids(text: str, *, limit: int = 8) -> list[str]:
    found = _ASSET_ID_RE.findall(text or "")
    return list(dict.fromkeys(found))[:limit]


def _pdf_preview_dir() -> Path:
    try:
        from app.config import settings

        base = Path(settings.uploads_dir) / "pdf_previews"
    except Exception:  # noqa: BLE001
        base = Path("/data/uploads/pdf_previews")
    base.mkdir(parents=True, exist_ok=True)
    return base


def render_pdf_page_to_png(
    pdf_path: Path,
    *,
    asset_id: str | None = None,
    page_index: int = 0,
    max_side: int = 1800,
) -> Path | None:
    """Rendert eine PDF-Seite als PNG (für Vision / Konzept-Grundriss)."""
    if not pdf_path.is_file() or pdf_path.suffix.lower() not in _PDF_SUFFIXES:
        return None
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # type: ignore  # PyMuPDF legacy
        except ImportError:
            logger.warning(
                "PyMuPDF fehlt – PDF-Grundrisse können nicht als Bild gelesen werden. "
                "pip install pymupdf"
            )
            return None

    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (asset_id or pdf_path.stem))[:48]
    out = _pdf_preview_dir() / f"{safe}_p{page_index}.png"
    try:
        # Cache: PDF neuer als Preview → neu rendern
        if out.is_file() and out.stat().st_mtime >= pdf_path.stat().st_mtime:
            return out
    except OSError:
        pass

    try:
        doc = fitz.open(pdf_path)
        try:
            if page_index < 0 or page_index >= len(doc):
                return None
            page = doc.load_page(page_index)
            rect = page.rect
            long_side = max(float(rect.width), float(rect.height), 1.0)
            # ~150–200 DPI equivalent via matrix
            zoom = min(2.5, max_side / long_side)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            pix.save(str(out))
        finally:
            doc.close()
        logger.info("PDF-Seite gerendert: %s → %s", pdf_path.name, out.name)
        return out if out.is_file() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF-Rendering fehlgeschlagen (%s): %s", pdf_path.name, exc)
        return None


def extract_pdf_text_snippet(pdf_path: Path, *, max_chars: int = 2500) -> str:
    """Kurzer Text aus PDF (Maße/Beschriftungen), falls extrahierbar."""
    try:
        from app.services.file_context import extract_file_context

        raw = extract_file_context(pdf_path, "pdf")
        # Nur den Textteil, nicht die Meta-Zeilen
        if "Extrahierter PDF-Text:" in raw:
            text = raw.split("Extrahierter PDF-Text:", 1)[1].strip()
        else:
            text = raw
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        logger.debug("PDF-Text nicht lesbar (%s): %s", pdf_path.name, exc)
        return ""


def load_reference_image_assets(
    asset_ids: list[str],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Lädt angehängte Inventar-Bilder und PDF-Grundrisse (PDF → gerenderte Seite).

    PDFs werden priorisiert, damit ein Grundriss nicht von vielen Raumfotos
    aus dem Limit gedrängt wird.
    """
    if not asset_ids:
        return []
    try:
        from app.db.postgres import SessionLocal
        from app.models.unprocessed_asset import UnprocessedAsset
        from app.models.unprocessed_asset import AssetFileType
    except Exception as exc:  # noqa: BLE001
        logger.warning("Referenz-Assets nicht ladbar: %s", exc)
        return []

    images: list[dict[str, Any]] = []
    pdfs: list[dict[str, Any]] = []
    db = SessionLocal()
    try:
        for raw_id in asset_ids:
            try:
                asset = db.get(UnprocessedAsset, uuid.UUID(raw_id))
            except ValueError:
                continue
            if asset is None or not asset.file_path:
                continue
            path = Path(asset.file_path)
            if not path.is_file():
                logger.warning("Referenz-Asset %s: Datei fehlt (%s)", raw_id, path)
                continue
            suffix = path.suffix.lower()
            file_type = getattr(asset.file_type, "value", str(asset.file_type))
            title = asset.title or path.name
            status = getattr(asset.status, "value", str(asset.status))
            aid = str(asset.id)

            is_pdf = file_type == AssetFileType.PDF.value or file_type == "pdf" or suffix in _PDF_SUFFIXES
            is_image = file_type == "image" or suffix in _IMAGE_SUFFIXES

            if is_pdf:
                rendered = render_pdf_page_to_png(path, asset_id=aid, page_index=0)
                if rendered is None:
                    logger.warning(
                        "PDF-Referenz %s (%s) konnte nicht gerendert werden – wird für Vision übersprungen",
                        aid,
                        path.name,
                    )
                    continue
                pdf_text = extract_pdf_text_snippet(path)
                # Titel so kennzeichnen, dass Floorplan-Heuristik greift
                plan_title = title if "grundriss" in title.lower() else f"Grundriss PDF: {title}"
                pdfs.append(
                    {
                        "id": aid,
                        "title": plan_title,
                        "file_path": str(rendered),
                        "path": rendered,
                        "source_path": str(path),
                        "file_type": "pdf",
                        "status": status,
                        "is_floorplan_pdf": True,
                        "pdf_text": pdf_text,
                    }
                )
                continue

            if not is_image:
                continue
            images.append(
                {
                    "id": aid,
                    "title": title,
                    "file_path": str(path),
                    "path": path,
                    "file_type": file_type,
                    "status": status,
                    "is_floorplan_pdf": False,
                }
            )
    finally:
        db.close()

    # PDFs zuerst, dann Fotos – Limit einhalten
    ordered = pdfs + images
    selected = ordered[:limit]
    if pdfs and not any(x.get("is_floorplan_pdf") for x in selected):
        # Mindestens ein PDF behalten
        selected = (pdfs[:1] + images)[:limit]
    if any(x.get("is_floorplan_pdf") for x in selected):
        logger.info(
            "Referenz-Assets: %s PDF-Grundriss(e), %s Foto(s) geladen",
            sum(1 for x in selected if x.get("is_floorplan_pdf")),
            sum(1 for x in selected if not x.get("is_floorplan_pdf")),
        )
    return selected


def prepare_image_bytes_for_vision(path: Path, *, max_side: int = 1280, max_bytes: int = 900_000) -> tuple[bytes, str] | None:
    """Komprimiertes JPEG für Multimodal-LLM / Logging."""
    # Falls direkt eine PDF übergeben wird: erste Seite rendern
    if path.suffix.lower() in _PDF_SUFFIXES:
        rendered = render_pdf_page_to_png(path, page_index=0, max_side=max_side)
        if rendered is None:
            return None
        path = rendered
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            scale = min(1.0, max_side / max(w, h, 1))
            if scale < 1.0:
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            quality = 72
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            while buf.tell() > max_bytes and quality > 45:
                quality -= 8
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision-Prep fehlgeschlagen (%s): %s", path.name, exc)
        try:
            raw = path.read_bytes()
            if len(raw) <= max_bytes:
                mt = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                return raw, mt
        except OSError:
            pass
        return None
