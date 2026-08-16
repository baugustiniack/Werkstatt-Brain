"""Konzept-Foto-Generierung für das Freigabe-Gate.

Nutzt OpenAI Images. Angehängte Inventar-Fotos (ids im User-Prompt) werden als
echte Bild-Referenzen über `images.edit` mitgegeben – nicht nur Textbeschreibungen.
Ohne Referenzen: klassische Text→Bild-Generierung.
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import settings_store

logger = logging.getLogger(__name__)

_SAFE_SESSION = re.compile(r"[^a-zA-Z0-9_-]+")
_ASSET_ID_RE = re.compile(
    r"(?:id=|/items/)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".heif"}


def is_image_gen_configured() -> bool:
    return bool(settings_store.resolve_openai_api_key())


def _safe_session_id(session_id: str) -> str:
    return _SAFE_SESSION.sub("_", session_id)[:64] or "session"


def _concepts_dir() -> Path:
    path = Path(settings.uploads_dir) / "concepts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extract_reference_asset_ids(user_prompt: str) -> list[str]:
    """Zieht Inventar-/Upload-Asset-IDs aus dem Prompt (Referenz-Anhänge)."""
    found = _ASSET_ID_RE.findall(user_prompt or "")
    return list(dict.fromkeys(found))[:8]


def _load_reference_image_paths(asset_ids: list[str]) -> list[Path]:
    if not asset_ids:
        return []
    try:
        from app.db.postgres import SessionLocal
        from app.models.unprocessed_asset import UnprocessedAsset
    except Exception as exc:  # noqa: BLE001
        logger.warning("Referenz-Assets nicht ladbar: %s", exc)
        return []

    paths: list[Path] = []
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
                continue
            suffix = path.suffix.lower()
            file_type = getattr(asset.file_type, "value", str(asset.file_type))
            if file_type == "image" or suffix in _IMAGE_SUFFIXES:
                paths.append(path)
            if len(paths) >= 6:
                break
    finally:
        db.close()
    return paths


def _prepare_reference_for_api(src: Path) -> Path | None:
    """Konvertiert/komprimiert Referenzbild zu JPEG/PNG für die Images-API."""
    try:
        from PIL import Image

        with Image.open(src) as img:
            img = img.convert("RGB")
            w, h = img.size
            scale = min(1.0, 1536 / max(w, h, 1))
            if scale < 1.0:
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
            import os

            os.close(fd)
            out = Path(tmp_name)
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Referenzbild %s nicht vorbereitbar: %s", src.name, exc)
        if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return src
        return None


def _build_image_prompt(
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
    *,
    reference_count: int = 0,
) -> str:
    materials: list[str] = []
    names: list[str] = []
    for part in parts[:12]:
        name = str(part.get("name") or "part").strip()
        names.append(name)
        mat = (part.get("material_tool_constraints") or {}).get("material_type")
        if mat:
            materials.append(str(mat))
    material_hint = ", ".join(dict.fromkeys(materials)) or "wood furniture panels"
    parts_hint = ", ".join(names[:8])
    if len(names) > 8:
        parts_hint += f" and {len(names) - 8} more components"

    prompt_excerpt = (user_prompt or "")[:2500]

    base = (
        f"Photorealistic interior photograph of a finished {project_title or 'custom furniture piece'} "
        f"installed in a real residential room that matches the request. "
        f"User request (German, for context): {prompt_excerpt}. "
        f"Materials look like: {material_hint}. "
        f"The piece is assembled as one coherent furniture object (not exploded parts). "
        f"Components include: {parts_hint}. "
        "Natural daylight, tasteful modern European home interior, magazine-quality product photography, "
        "no text overlays, no labels, no technical drawings, no blueprint, no wireframe."
    )
    if reference_count > 0:
        refs = ", ".join(f"Image {i}" for i in range(1, reference_count + 1))
        base += (
            f" CRITICAL: Use the {reference_count} attached reference photo(s) ({refs}) as ground truth "
            "for room layout, existing furniture, materials, colors, guitar/instruments, walls and proportions. "
            "The generated scene must clearly resemble those references (same space and objects), "
            "extended with the requested CNC furniture. Do not invent an unrelated room."
        )
    return base


def _write_concept_bytes(session_id: str, image_bytes: bytes) -> str:
    safe_id = _safe_session_id(session_id)
    concepts_dir = _concepts_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    versioned = concepts_dir / f"{safe_id}_{stamp}.png"
    latest = concepts_dir / f"{safe_id}.png"
    versioned.write_bytes(image_bytes)
    try:
        shutil.copy2(versioned, latest)
    except OSError:
        latest.write_bytes(image_bytes)
    return f"/api/v1/cad/concept-image/{safe_id}"


def _response_to_bytes(response: Any) -> bytes | None:
    data = response.data[0]
    b64 = getattr(data, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    if getattr(data, "url", None):
        import urllib.request

        with urllib.request.urlopen(data.url, timeout=60) as resp:  # noqa: S310
            return resp.read()
    return None


def generate_concept_image(
    session_id: str,
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
) -> str | None:
    """Generiert ein Konzept-Foto; nutzt Inventar-Referenzbilder wenn IDs im Prompt stehen."""
    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        return None

    asset_ids = extract_reference_asset_ids(user_prompt)
    raw_refs = _load_reference_image_paths(asset_ids)
    prepared: list[Path] = []
    temp_paths: list[Path] = []
    for src in raw_refs:
        prepped = _prepare_reference_for_api(src)
        if prepped is None:
            continue
        prepared.append(prepped)
        if prepped != src:
            temp_paths.append(prepped)

    prompt = _build_image_prompt(
        project_title,
        user_prompt,
        parts,
        reference_count=len(prepared),
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        image_bytes: bytes | None = None

        if prepared:
            handles = [p.open("rb") for p in prepared]
            try:
                logger.info(
                    "Konzept-Foto mit %s Referenzbild(ern) via images.edit (session=%s)",
                    len(prepared),
                    session_id,
                )
                try:
                    response = client.images.edit(
                        model="gpt-image-1",
                        image=handles,
                        prompt=prompt,
                        size="1024x1024",
                        input_fidelity="high",
                    )
                except TypeError:
                    # ältere SDK ohne input_fidelity
                    for h in handles:
                        h.seek(0)
                    response = client.images.edit(
                        model="gpt-image-1",
                        image=handles,
                        prompt=prompt,
                        size="1024x1024",
                    )
                except Exception as edit_exc:  # noqa: BLE001
                    logger.info("images.edit fehlgeschlagen (%s) – Fallback text-only generate", edit_exc)
                    response = None

                if response is not None:
                    image_bytes = _response_to_bytes(response)
            finally:
                for h in handles:
                    try:
                        h.close()
                    except OSError:
                        pass

        if image_bytes is None:
            try:
                response = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt,
                    size="1024x1024",
                    n=1,
                )
            except Exception as primary_exc:  # noqa: BLE001
                logger.info("gpt-image-1 generate fehlgeschlagen (%s) – versuche dall-e-3", primary_exc)
                response = client.images.generate(
                    model="dall-e-3",
                    prompt=prompt[:4000],
                    size="1024x1024",
                    n=1,
                    response_format="b64_json",
                )
            image_bytes = _response_to_bytes(response)

        if not image_bytes:
            logger.warning("OpenAI Images lieferte keine Bilddaten")
            return None

        return _write_concept_bytes(session_id, image_bytes)
    except Exception as exc:  # noqa: BLE001 - Bildgenerierung darf den Graph nicht stoppen
        logger.warning("Konzept-Foto-Generierung fehlgeschlagen: %s", exc)
        return None
    finally:
        for tmp in temp_paths:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def concept_image_path(session_id: str) -> Path | None:
    """Neuester Konzept-Pfad der Session (Latest-Pointer)."""
    safe_id = _safe_session_id(session_id)
    path = _concepts_dir() / f"{safe_id}.png"
    if path.is_file():
        return path
    versions = sorted(_concepts_dir().glob(f"{safe_id}_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return versions[0] if versions else None


def list_concept_image_versions(session_id: str) -> list[Path]:
    """Alle Konzept-Versionen einer Session (älteste zuerst)."""
    safe_id = _safe_session_id(session_id)
    versions = sorted(_concepts_dir().glob(f"{safe_id}_*.png"), key=lambda p: p.name)
    latest = _concepts_dir() / f"{safe_id}.png"
    if latest.is_file() and latest not in versions:
        versions.append(latest)
    return versions
