"""Extrahiert lesbaren Text-/Metadaten-Kontext aus Inventar-Dateien für die
KI-Beschreibung (PDF, CAD-ASCII, Text, Binär-Fallback)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_EXTRACT_CHARS = 12000


def _safe_text_read(path: Path, *, max_chars: int = _MAX_EXTRACT_CHARS) -> str | None:
    try:
        raw = path.read_bytes()[: max_chars * 4]
        for encoding in ("utf-8", "latin-1"):
            try:
                text = raw.decode(encoding)
                return text[:max_chars]
            except UnicodeDecodeError:
                continue
    except OSError as exc:
        logger.warning("Datei nicht lesbar (%s): %s", path, exc)
    return None


def _extract_pdf_text(path: Path, *, max_chars: int = _MAX_EXTRACT_CHARS) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        chunks: list[str] = []
        total = 0
        for page in reader.pages[:20]:
            page_text = (page.extract_text() or "").strip()
            if not page_text:
                continue
            chunks.append(page_text)
            total += len(page_text)
            if total >= max_chars:
                break
        text = "\n\n".join(chunks).strip()
        if text:
            return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF-Text-Extraktion fehlgeschlagen (%s): %s", path.name, exc)
    return ""


def _stl_summary(path: Path) -> str:
    size = path.stat().st_size
    try:
        with path.open("rb") as fh:
            head = fh.read(200)
            if head.lstrip().lower().startswith(b"solid"):
                text = _safe_text_read(path, max_chars=4000) or ""
                return f"ASCII-STL-Kopf:\n{text[:1500]}"
            # Binary STL: nur 84 Bytes (Header + Triangle-Count), nie die ganze Datei
            fh.seek(0)
            header = fh.read(80)
            count_bytes = fh.read(4)
    except OSError as exc:
        return f"STL nicht lesbar ({size} Bytes): {exc}"

    tri_count = int.from_bytes(count_bytes, "little") if len(count_bytes) == 4 else None
    header_txt = header.decode("latin-1", errors="replace").strip("\x00").strip()
    return (
        f"Binäres STL, Größe {size} Bytes"
        + (f", ~{tri_count} Dreiecke" if tri_count is not None else "")
        + (f", Header: {header_txt!r}" if header_txt else "")
    )


def extract_file_context(path: Path, file_type: str) -> str:
    """Liefert Textkontext für die LLM-Beschreibung."""
    if not path.is_file():
        return f"Datei fehlt: {path}"

    suffix = path.suffix.lower()
    size = path.stat().st_size
    meta = f"Dateiname: {path.name}\nTyp: {file_type}\nEndung: {suffix}\nGröße: {size} Bytes"

    if file_type == "pdf" or suffix == ".pdf":
        text = _extract_pdf_text(path)
        if text:
            return f"{meta}\n\nExtrahierter PDF-Text:\n{text}"
        return f"{meta}\n\n(Kein Text aus PDF extrahierbar – bitte Dateiname und Kontext nutzen.)"

    if file_type == "stl" or suffix == ".stl":
        return f"{meta}\n\n{_stl_summary(path)}"

    if file_type in ("step", "f3d") or suffix in (".step", ".stp", ".iges", ".igs", ".dxf"):
        text = _safe_text_read(path, max_chars=8000)
        if text:
            return f"{meta}\n\nDatei-Inhalt (Auszug):\n{text}"
        return f"{meta}\n\n(Binäre/komprimierte CAD-Datei – nur Metadaten verfügbar.)"

    if suffix in (
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".svg",
        ".html",
        ".htm",
        ".log",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".obj",
        ".mtl",
    ):
        text = _safe_text_read(path) or ""
        return f"{meta}\n\nDatei-Inhalt:\n{text}" if text else meta

    # Generischer Versuch: Text lesen, sonst nur Meta
    text = _safe_text_read(path, max_chars=4000)
    if text and sum(1 for c in text[:500] if c.isprintable() or c.isspace()) > 400:
        return f"{meta}\n\nInhalt (Auszug):\n{text}"
    return f"{meta}\n\n(Binärdatei – Beschreibung aus Dateiname und Typ ableiten.)"
