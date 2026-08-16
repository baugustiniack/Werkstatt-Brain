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
    from app.services.reference_assets import extract_reference_asset_ids as _extract

    return _extract(user_prompt, limit=8)


def _load_reference_image_paths(asset_ids: list[str]) -> list[Path]:
    from app.services.reference_assets import load_reference_image_assets

    assets = load_reference_image_assets(asset_ids, limit=4)
    return [a["path"] for a in assets if a.get("path")]


def _prepare_reference_for_api(src: Path) -> Path | None:
    """Konvertiert/komprimiert Referenzbild zu JPEG/PNG für die Images-API."""
    try:
        from PIL import Image

        with Image.open(src) as img:
            img = img.convert("RGB")
            w, h = img.size
            scale = min(1.0, 1024 / max(w, h, 1))
            if scale < 1.0:
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            fd, tmp_name = tempfile.mkstemp(suffix=".jpg")
            import os

            os.close(fd)
            out = Path(tmp_name)
            img.save(out, format="JPEG", quality=72, optimize=True)
            # Harte Obergrenze für API-Payload (Host-Schutz)
            if out.stat().st_size > 700_000:
                img.save(out, format="JPEG", quality=55, optimize=True)
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


def _write_concept_bytes(session_id: str, image_bytes: bytes, *, view_key: str | None = None) -> str:
    safe_id = _safe_session_id(session_id)
    concepts_dir = _concepts_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    suffix = f"_{view_key}" if view_key else ""
    versioned = concepts_dir / f"{safe_id}_{stamp}{suffix}.png"
    versioned.write_bytes(image_bytes)
    # Primary / Latest für overview
    if not view_key or view_key in ("overview", "primary"):
        latest = concepts_dir / f"{safe_id}.png"
        try:
            shutil.copy2(versioned, latest)
        except OSError:
            latest.write_bytes(image_bytes)
        # zusätzlich stabile overview-Datei
        named = concepts_dir / f"{safe_id}_overview.png"
        try:
            shutil.copy2(versioned, named)
        except OSError:
            named.write_bytes(image_bytes)
        return f"/api/v1/cad/concept-image/{safe_id}/view/overview"

    named = concepts_dir / f"{safe_id}{suffix}.png"
    try:
        shutil.copy2(versioned, named)
    except OSError:
        named.write_bytes(image_bytes)
    # Pfad-URL (ohne Query) – zuverlässiger für Galerie/Proxy/Cache
    return f"/api/v1/cad/concept-image/{safe_id}/view/{view_key}"


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


def _looks_like_floorplan_image(path: Path, title: str = "") -> bool:
    """Heuristik: Nutzer-Grundriss vs. Raumfoto."""
    hint = f"{title} {path.name}".lower()
    if any(k in hint for k in ("grundriss", "floorplan", "floor_plan", "bemaß", "bemass", "massstab", "maßstab")):
        return True
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as img:
            rgb = img.convert("RGB")
            small = rgb.resize((96, 96))
            stat = ImageStat.Stat(small)
            # hohe Helligkeit + geringe Farbsättigung → oft Planzeichnung
            mean = sum(stat.mean) / 3.0
            # stddev niedrig = flächig
            std = sum(stat.stddev) / 3.0
            if mean > 200 and std < 45:
                return True
            if mean > 180 and std < 35:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _split_reference_paths(
    paths: list[Path],
    *,
    titles: list[str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """(raumfotos, grundrisse)."""
    photos: list[Path] = []
    plans: list[Path] = []
    titles = titles or [""] * len(paths)
    for path, title in zip(paths, titles, strict=False):
        if _looks_like_floorplan_image(path, title):
            plans.append(path)
        else:
            photos.append(path)
    return photos, plans


def _build_view_prompt(
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
    view: dict[str, Any],
    *,
    interior_brief: dict[str, Any] | None = None,
    reference_count: int = 0,
) -> str:
    kind = str(view.get("kind") or "overview")
    label = str(view.get("label") or kind)
    focus = str(view.get("focus") or "")
    part_name = view.get("part_name")
    room_summary = ""
    vision_brief = ""
    if isinstance(interior_brief, dict):
        room_summary = str(interior_brief.get("room_summary") or "")
        spatial = interior_brief.get("spatial_notes") or []
        if isinstance(spatial, list) and spatial:
            room_summary += " " + "; ".join(str(s) for s in spatial[:4])
        vision_brief = str(interior_brief.get("reference_vision_brief") or "")[:1800]

    prompt_excerpt = (user_prompt or "")[:1800]
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

    if kind == "floorplan":
        if reference_count > 0:
            base = (
                f"Edit the user's attached floor-plan / room references into a clean annotated "
                f"2D top-down floor plan for project '{project_title or 'furniture layout'}'. "
                f"If a drawn plan is attached, keep its walls, proportions and dimension labels. "
                f"If only room photos are attached, derive a plausible top-down plan of THAT room. "
                f"Add furniture footprints for: {parts_hint}. "
                f"User request: {prompt_excerpt}. Focus: {focus or 'placement'}. "
                f"Room context: {room_summary or 'as in the attached plan/photos'}. "
                "White background, readable dimensions, no photorealism, no 3D perspective, no people."
            )
        else:
            base = (
                f"Clean annotated 2D floor plan for project '{project_title or 'furniture layout'}'. "
                f"Furniture footprints: {parts_hint}. User request: {prompt_excerpt}. "
                f"Focus: {focus or 'placement'}. Room: {room_summary or 'as described'}. "
                "White background, dimensions, no photorealism."
            )
    elif kind == "part" and part_name:
        base = (
            f"Photorealistic interior photograph focusing on '{part_name}' "
            f"in the SAME room and the SAME furniture concept as the attached images "
            f"(project '{project_title}'). "
            f"User request: {prompt_excerpt}. Materials: {material_hint}. Focus: {focus}. "
            f"Room context: {room_summary or 'match references exactly'}. "
            "CRITICAL DESIGN LOCK: If an overview/concept image is attached, keep that EXACT "
            "cabinet/furniture design (doors, color panels, proportions, wood tone). "
            "Do NOT invent a different wardrobe variant. Only change camera angle / crop. "
            "CRITICAL PLACEMENT: Match the user's photo placement exactly — if the cabinet "
            "stands left of the room corner (corner wall / guitar / amp to the right of the cabinet), "
            "keep it left of that corner; do not mirror or relocate it. "
            "Natural daylight, no text overlays, no blueprint."
        )
    else:
        base = (
            f"Photorealistic interior photograph of the complete concept "
            f"'{project_title or 'custom furniture'}' inside the SAME room as the attached reference photos. "
            f"User request: {prompt_excerpt}. Materials: {material_hint}. Components: {parts_hint}. "
            f"Focus: {focus or label}. Room context: {room_summary or 'as in references'}. "
            "Match reference room layout, colors and existing objects exactly; "
            "only add/modify the requested CNC furniture as ONE coherent design "
            "(not multiple alternative cabinets). "
            "CRITICAL PLACEMENT: Preserve left/right placement from the user photos — "
            "if the main cabinet is left of the corner, keep it left of the corner "
            "(do not flip the scene). Natural daylight, no text overlays, no blueprint."
        )

    if reference_count > 0:
        refs = ", ".join(f"Image {i}" for i in range(1, reference_count + 1))
        base += (
            f" CRITICAL: {refs} are the user's real photos/plan. "
            "Do not invent a different room. Preserve recognizable objects from the references."
        )
    if vision_brief:
        # Brief zuerst im Prompt verankern (Modelle gewichten Anfang stärker)
        base = (
            "GROUND TRUTH from the user's real photos/floorplan (highest priority — "
            "do not invent a different room):\n"
            f"{vision_brief}\n\n" + base
        )
    return base


def _generate_single_image(
    client: Any,
    prompt: str,
    prepared: list[Path],
) -> bytes | None:
    image_bytes: bytes | None = None
    if prepared:
        handles = [p.open("rb") for p in prepared]
        try:
            try:
                response = client.images.edit(
                    model="gpt-image-1",
                    image=handles,
                    prompt=prompt,
                    size="1024x1024",
                    input_fidelity="high",
                )
            except TypeError:
                for h in handles:
                    h.seek(0)
                response = client.images.edit(
                    model="gpt-image-1",
                    image=handles,
                    prompt=prompt,
                    size="1024x1024",
                )
            except Exception as edit_exc:  # noqa: BLE001
                logger.info("images.edit fehlgeschlagen (%s) – Fallback generate", edit_exc)
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
            logger.info("gpt-image-1 generate fehlgeschlagen (%s) – dall-e-3", primary_exc)
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt[:4000],
                size="1024x1024",
                n=1,
                response_format="b64_json",
            )
        image_bytes = _response_to_bytes(response)
    return image_bytes


def generate_concept_gallery(
    session_id: str,
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
    *,
    views: list[dict[str, Any]] | None = None,
    interior_brief: dict[str, Any] | None = None,
    max_images: int = 5,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Erzeugt mehrere Konzept-Ansichten (Übersicht, Teile, optional Grundriss). Seriell, max. N."""
    import time

    api_key = settings_store.resolve_openai_api_key()
    if not api_key:
        return []

    view_list = [dict(v) for v in (views or []) if isinstance(v, dict)]
    if not view_list:
        view_list = [
            {
                "kind": "overview",
                "label": "Raumsituation",
                "part_name": None,
                "focus": "Gesamtsituation",
            }
        ]
    view_list = view_list[: max(1, min(max_images, 5))]

    ids = list(asset_ids or []) or extract_reference_asset_ids(user_prompt)
    # Titel mitladen für Grundriss-Erkennung
    from app.services.reference_assets import load_reference_image_assets

    asset_rows = load_reference_image_assets(ids, limit=6)
    raw_refs = [a["path"] for a in asset_rows if a.get("path")]
    ref_titles = [str(a.get("title") or "") for a in asset_rows]
    if ids:
        logger.info(
            "Konzept-Galerie: %s Asset-ID(s), %s Bilddatei(en) geladen (session=%s)",
            len(ids),
            len(raw_refs),
            session_id,
        )
    prepared_photos: list[Path] = []
    prepared_plans: list[Path] = []
    temp_paths: list[Path] = []
    photos, plans = _split_reference_paths(raw_refs, titles=ref_titles)
    logger.info(
        "Konzept-Referenzen: %s Raumfoto(s), %s Grundriss(e)",
        len(photos),
        len(plans),
    )
    for src in photos[:3]:
        prepped = _prepare_reference_for_api(src)
        if prepped is None:
            continue
        prepared_photos.append(prepped)
        if prepped != src:
            temp_paths.append(prepped)
    for src in plans[:2]:
        prepped = _prepare_reference_for_api(src)
        if prepped is None:
            continue
        prepared_plans.append(prepped)
        if prepped != src:
            temp_paths.append(prepped)

    results: list[dict[str, Any]] = []
    overview_lock: Path | None = None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        for i, view in enumerate(view_list):
            kind = str(view.get("kind") or f"view{i}")
            label = str(view.get("label") or kind)
            view_key = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{kind}_{i}")[:40]
            if kind == "overview" and i == 0:
                view_key = "overview"
            # Fotos für fotorealistische Views; Grundriss-Zeichnung für Plan-View
            if kind == "floorplan":
                use_refs = list(prepared_plans or prepared_photos)
            elif kind == "part" and overview_lock is not None:
                # Übersicht zuerst = Design-Lock, dann Nutzerfotos
                use_refs = [overview_lock, *prepared_photos][:4] or [overview_lock]
            else:
                use_refs = list(prepared_photos or prepared_plans)
            prompt = _build_view_prompt(
                project_title,
                user_prompt,
                parts,
                view,
                interior_brief=interior_brief,
                reference_count=len(use_refs),
            )
            if kind == "part" and overview_lock is not None:
                prompt = (
                    "Image 1 is the locked OVERVIEW of this exact concept. "
                    "Copy that furniture design 1:1; only reframe for the requested focus.\n\n"
                    + prompt
                )
            try:
                logger.info(
                    "Konzept-Ansicht %s/%s kind=%s refs=%s (photos=%s plans=%s lock=%s) session=%s",
                    i + 1,
                    len(view_list),
                    kind,
                    len(use_refs),
                    len(prepared_photos),
                    len(prepared_plans),
                    bool(overview_lock),
                    session_id,
                )
                image_bytes = _generate_single_image(client, prompt, use_refs)
                if not image_bytes:
                    continue
                url = _write_concept_bytes(session_id, image_bytes, view_key=view_key)
                results.append(
                    {
                        "url": url,
                        "label": label,
                        "kind": kind,
                        "part_name": view.get("part_name"),
                        "view_key": view_key,
                    }
                )
                if kind == "overview" and overview_lock is None:
                    lock = Path(tempfile.gettempdir()) / f"concept_lock_{session_id[:12]}.png"
                    lock.write_bytes(image_bytes)
                    overview_lock = lock
                    temp_paths.append(lock)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Konzept-Ansicht %s fehlgeschlagen: %s", kind, exc)
            if i < len(view_list) - 1:
                time.sleep(0.6)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konzept-Galerie fehlgeschlagen: %s", exc)
    finally:
        for tmp in temp_paths:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    logger.info("Konzept-Galerie fertig: %s Ansicht(en) für session=%s", len(results), session_id)
    return results


def generate_concept_image(
    session_id: str,
    project_title: str,
    user_prompt: str,
    parts: list[dict[str, Any]],
    *,
    interior_brief: dict[str, Any] | None = None,
) -> str | None:
    """Kompatibilität: erzeugt Galerie und gibt die erste URL zurück."""
    gallery = generate_concept_gallery(
        session_id,
        project_title,
        user_prompt,
        parts,
        views=(interior_brief or {}).get("suggested_views") if interior_brief else None,
        interior_brief=interior_brief,
        max_images=1,
    )
    return gallery[0]["url"] if gallery else None


def concept_image_path(session_id: str, *, view: str | None = None) -> Path | None:
    """Konzept-Pfad: Latest oder spezifische Ansicht."""
    safe_id = _safe_session_id(session_id)
    if view:
        for candidate in (
            _concepts_dir() / f"{safe_id}_{view}.png",
            _concepts_dir() / f"{safe_id}_overview.png" if view == "overview" else None,
        ):
            if candidate is not None and candidate.is_file():
                return candidate
        matches = sorted(
            _concepts_dir().glob(f"{safe_id}_*{view}*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if matches:
            return matches[0]
        if view == "overview":
            latest = _concepts_dir() / f"{safe_id}.png"
            if latest.is_file():
                return latest
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


def _view_label_from_key(view_key: str) -> tuple[str, str]:
    """(kind, label) aus Datei-Suffix."""
    key = (view_key or "").lower()
    if key in ("overview", "primary", ""):
        return "overview", "Raumsituation"
    if key.startswith("floorplan"):
        return "floorplan", "Grundriss"
    if key.startswith("part"):
        return "part", f"Ansicht {view_key}"
    return key or "view", view_key or "Ansicht"


def list_concept_gallery(session_id: str) -> list[dict[str, Any]]:
    """Galerie aus Disk rekonstruieren (Fallback, wenn State die Liste verloren hat)."""
    safe_id = _safe_session_id(session_id)
    concepts = _concepts_dir()
    found: dict[str, Path] = {}

    overview = concepts / f"{safe_id}_overview.png"
    latest = concepts / f"{safe_id}.png"
    if overview.is_file():
        found["overview"] = overview
    elif latest.is_file():
        found["overview"] = latest

    stamp_re = re.compile(r"^\d{8}T\d+")
    for path in concepts.glob(f"{safe_id}_*.png"):
        rest = path.name[len(safe_id) + 1 : -4]
        if not rest:
            continue
        # {stamp}_{view} oder nur {view}
        if stamp_re.match(rest) and "_" in rest:
            view_key = rest.split("_", 1)[1]
            is_stable = False
        else:
            view_key = rest
            is_stable = True
        if not view_key or view_key == "overview":
            # overview bereits oben
            if view_key == "overview" and "overview" not in found:
                found["overview"] = path
            continue
        prev = found.get(view_key)
        if prev is None:
            found[view_key] = path
        elif is_stable:
            found[view_key] = path

    order_keys = sorted(
        found.keys(),
        key=lambda k: (
            0 if k == "overview" else 1 if str(k).startswith("floorplan") else 2,
            k,
        ),
    )
    out: list[dict[str, Any]] = []
    for view_key in order_keys:
        kind, label = _view_label_from_key(view_key)
        out.append(
            {
                "url": f"/api/v1/cad/concept-image/{safe_id}/view/{view_key}",
                "label": label,
                "kind": kind,
                "view_key": view_key,
            }
        )
    return out


def ensure_gallery_urls(
    session_id: str,
    gallery: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Nutzt State-Galerie; ergänzt/ersetzt aus Disk wenn dort mehr Ansichten liegen."""
    current = list(gallery or [])
    rebuilt = list_concept_gallery(session_id)
    if not rebuilt:
        return current
    if len(rebuilt) > len(current):
        logger.info(
            "Konzept-Galerie aus Disk: %s Ansicht(en) (State hatte %s) session=%s",
            len(rebuilt),
            len(current),
            session_id,
        )
        return rebuilt
    if not current:
        logger.info(
            "Konzept-Galerie aus Disk rekonstruiert: %s Ansicht(en) session=%s",
            len(rebuilt),
            session_id,
        )
        return rebuilt
    return current
