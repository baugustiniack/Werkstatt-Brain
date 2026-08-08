"""Konzept-Foto-Generierung für das Freigabe-Gate (Nutzer-Feedback: ansprechendes
Foto des fertigen Möbels in der richtigen Umgebung statt Teile-SVG als Hauptansicht).

Nutzt die OpenAI Images API, sofern ein Key konfiguriert ist (UI-Setting oder
`.env`). Ohne Key/bei Fehler liefert `None` – der Workflow läuft weiter mit
Textzusammenfassung + optionalem SVG-Fallback.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import settings_store

logger = logging.getLogger(__name__)

_SAFE_SESSION = re.compile(r"[^a-zA-Z0-9_-]+")


def is_image_gen_configured() -> bool:
    return bool(settings_store.resolve_openai_api_key())


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
    """Generiert ein Konzept-Foto und speichert es unter uploads/concepts/.

    Rückgabe: relativer API-Pfad `/api/v1/cad/concept-image/{session_id}` oder None.
    """
    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        return None

    safe_id = _SAFE_SESSION.sub("_", session_id)[:64] or "session"
    concepts_dir = Path(settings.uploads_dir) / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    dest = concepts_dir / f"{safe_id}.png"

    prompt = _build_image_prompt(project_title, user_prompt, parts)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        # gpt-image-1 bevorzugt; Fallback auf dall-e-3 falls das Modell nicht verfügbar ist.
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
            dest.write_bytes(base64.b64decode(b64))
        elif getattr(data, "url", None):
            import urllib.request

            with urllib.request.urlopen(data.url, timeout=60) as resp:  # noqa: S310 - OpenAI CDN URL
                dest.write_bytes(resp.read())
        else:
            logger.warning("OpenAI Images lieferte weder b64_json noch url")
            return None

        return f"/api/v1/cad/concept-image/{safe_id}"
    except Exception as exc:  # noqa: BLE001 - Bildgenerierung darf den Graph nicht stoppen
        logger.warning("Konzept-Foto-Generierung fehlgeschlagen: %s", exc)
        return None


def concept_image_path(session_id: str) -> Path | None:
    safe_id = _SAFE_SESSION.sub("_", session_id)[:64] or "session"
    path = Path(settings.uploads_dir) / "concepts" / f"{safe_id}.png"
    return path if path.is_file() else None
