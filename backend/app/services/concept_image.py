"""Konzept-Foto-Generierung für das Freigabe-Gate (Nutzer-Feedback: ansprechendes
Foto des fertigen Möbels in der richtigen Umgebung statt Teile-SVG als Hauptansicht).

Nutzt die OpenAI Images API, sofern ein Key konfiguriert ist (UI-Setting oder
`.env`). Ohne Key/bei Fehler liefert `None` – der Workflow läuft weiter mit
Textzusammenfassung + optionalem SVG-Fallback.

Versionierte Ablage: jedes neue Foto bleibt erhalten (`{session}_{timestamp}.png`);
`{session}.png` zeigt zusätzlich immer auf das neueste Foto (API-Kompatibilität).
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import settings_store

logger = logging.getLogger(__name__)

_SAFE_SESSION = re.compile(r"[^a-zA-Z0-9_-]+")


def is_image_gen_configured() -> bool:
    return bool(settings_store.resolve_openai_api_key())


def _safe_session_id(session_id: str) -> str:
    return _SAFE_SESSION.sub("_", session_id)[:64] or "session"


def _concepts_dir() -> Path:
    path = Path(settings.uploads_dir) / "concepts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_image_prompt(project_title: str, user_prompt: str, parts: list[dict[str, Any]]) -> str:
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

    return (
        f"Photorealistic interior photograph of a finished {project_title or 'custom furniture piece'} "
        f"installed in a real residential room that matches the request. "
        f"User request (German, for context): {user_prompt[:400]}. "
        f"Materials look like: {material_hint}. "
        f"The piece is assembled as one coherent furniture object (not exploded parts). "
        f"Components include: {parts_hint}. "
        "Natural daylight, tasteful modern European home interior, magazine-quality product photography, "
        "no text overlays, no labels, no technical drawings, no blueprint, no wireframe."
    )


def generate_concept_image(
    session_id: str,
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
) -> str | None:
    """Generiert ein Konzept-Foto und speichert es versioniert unter uploads/concepts/.

    Rückgabe: relativer API-Pfad `/api/v1/cad/concept-image/{session_id}` (zeigt auf
    das neueste Foto der Session) oder None.
    """
    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        return None

    safe_id = _safe_session_id(session_id)
    concepts_dir = _concepts_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    versioned = concepts_dir / f"{safe_id}_{stamp}.png"
    latest = concepts_dir / f"{safe_id}.png"

    prompt = _build_image_prompt(project_title, user_prompt, parts)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        try:
            response = client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size="1024x1024",
                n=1,
            )
        except Exception as primary_exc:  # noqa: BLE001
            logger.info("gpt-image-1 fehlgeschlagen (%s) – versuche dall-e-3", primary_exc)
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                n=1,
                response_format="b64_json",
            )

        data = response.data[0]
        b64 = getattr(data, "b64_json", None)
        if b64:
            versioned.write_bytes(base64.b64decode(b64))
        elif getattr(data, "url", None):
            import urllib.request

            with urllib.request.urlopen(data.url, timeout=60) as resp:  # noqa: S310 - OpenAI CDN URL
                versioned.write_bytes(resp.read())
        else:
            logger.warning("OpenAI Images lieferte weder b64_json noch url")
            return None

        # Latest-Pointer für bestehende API-Clients; Version bleibt zusätzlich erhalten
        try:
            shutil.copy2(versioned, latest)
        except OSError:
            latest.write_bytes(versioned.read_bytes())

        return f"/api/v1/cad/concept-image/{safe_id}"
    except Exception as exc:  # noqa: BLE001 - Bildgenerierung darf den Graph nicht stoppen
        logger.warning("Konzept-Foto-Generierung fehlgeschlagen: %s", exc)
        return None


def concept_image_path(session_id: str) -> Path | None:
    """Neuester Konzept-Pfad der Session (Latest-Pointer)."""
    safe_id = _safe_session_id(session_id)
    path = _concepts_dir() / f"{safe_id}.png"
    if path.is_file():
        return path
    # Fallback: neueste versionierte Datei
    versions = sorted(_concepts_dir().glob(f"{safe_id}_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return versions[0] if versions else None


def list_concept_image_versions(session_id: str) -> list[Path]:
    """Alle Konzept-Versionen einer Session (älteste zuerst)."""
    safe_id = _safe_session_id(session_id)
    versions = sorted(_concepts_dir().glob(f"{safe_id}_*.png"), key=lambda p: p.name)
    latest = _concepts_dir() / f"{safe_id}.png"
    if latest.is_file() and latest not in versions:
        # nur anhängen wenn Inhalt nicht schon als Version existiert
        versions.append(latest)
    return versions
