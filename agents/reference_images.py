"""Hilfen: Referenzbilder aus State/Prompt für Agent-LLM-Calls laden."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from agents.state import AgentState

logger = logging.getLogger(__name__)

_BRIEF_SYSTEM = """Du analysierst Referenzfotos/Grundrisse für eine CNC-/Möbel-Werkstatt-App.
Extrahiere nur, was in den Bildern sichtbar ist. Antworte auf Deutsch als klaren Fließtext
(Abschnitte mit Überschriften), max. ~400 Wörter:

1) Raum / Grundriss (Form, Orientierung) – markiere klar, ob ein bemessener Grundriss dabei ist
   (auch als gerenderte PDF-Seite / Architekturplan). Wenn ein Plan dabei ist: Maße und Wände daraus.
2) Ablesbare Maße (exakt wie beschriftet, Einheit beibehalten)
3) Vorhandene Möbel, Instrumente, Einbauten (Marke/Typ wenn erkennbar, z.B. IKEA)
4) Wände, Fenster, Türen, Besonderheiten
5) Vorgaben für Einbaumöbel / freistehende Stücke

Keine Spekulation. Keine Fragen. Keine CAD-Techniktipps.
Wenn sowohl Raumfotos als auch ein Grundriss/PDF-Plan vorliegen: beides auswerten und klar trennen.
"""

_FLOORPLAN_NAME_HINTS = (
    "grundriss",
    "floorplan",
    "floor_plan",
    "floor-plan",
    "bemaß",
    "bemass",
    "massstab",
    "maßstab",
)


def resolve_reference_asset_ids(state: AgentState) -> list[str]:
    from app.services.reference_assets import extract_reference_asset_ids

    existing = state.get("reference_asset_ids")
    if isinstance(existing, list) and existing:
        return [str(x) for x in existing if x][:8]
    return extract_reference_asset_ids(state.get("user_prompt") or "", limit=8)


def _looks_like_floorplan_asset(path: Path, title: str = "") -> bool:
    hint = f"{title} {path.name}".lower()
    if any(k in hint for k in _FLOORPLAN_NAME_HINTS):
        return True
    try:
        from PIL import Image, ImageStat

        with Image.open(path) as img:
            rgb = img.convert("RGB")
            small = rgb.resize((96, 96))
            stat = ImageStat.Stat(small)
            mean = sum(stat.mean) / 3.0
            std = sum(stat.stddev) / 3.0
            if mean > 200 and std < 45:
                return True
            if mean > 180 and std < 35:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def classify_reference_assets(state: AgentState, *, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
    """Teilt Chat-Anhänge in Raumfotos vs. Grundriss-Pläne."""
    from app.services.reference_assets import load_reference_image_assets

    ids = resolve_reference_asset_ids(state)
    rows = load_reference_image_assets(ids, limit=limit)
    photos: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    for row in rows:
        path = row.get("path")
        if not isinstance(path, Path):
            path = Path(str(row.get("file_path") or ""))
        title = str(row.get("title") or "")
        entry = {
            "id": str(row.get("id") or ""),
            "title": title,
            "path": path,
            "pdf_text": str(row.get("pdf_text") or ""),
            "is_floorplan_pdf": bool(row.get("is_floorplan_pdf")),
        }
        if row.get("is_floorplan_pdf") or _looks_like_floorplan_asset(path, title):
            plans.append(entry)
        else:
            photos.append(entry)
    return {"photos": photos, "plans": plans, "all": rows}


def build_reference_requirement_anchors(state: AgentState) -> list[dict[str, str]]:
    """Must-Requirements aus Anhängen (Grundriss/Fotos) – für VV & Concept."""
    classified = classify_reference_assets(state)
    plans = classified["plans"]
    photos = classified["photos"]
    brief = get_reference_vision_brief(state)
    reqs: list[dict[str, str]] = []

    for i, plan in enumerate(plans, start=1):
        title = plan.get("title") or "Grundriss"
        aid = plan.get("id") or "?"
        pdf_extra = ""
        pdf_text = str(plan.get("pdf_text") or "").strip()
        if pdf_text:
            pdf_extra = f" Extrahierbarer PDF-Text (Auszug): {pdf_text[:350]}"
        elif plan.get("is_floorplan_pdf"):
            pdf_extra = " Quelle: PDF-Anhang (Seite als Bild gerendert)."
        reqs.append(
            {
                "id": f"REF-PLAN-{i}",
                "text": (
                    f"Grundriss-Anhang „{title}“ (Asset {aid}) ist verbindliche Raum-Ground-Truth: "
                    "Wände, Proportionen und ablesbare Maße daraus übernehmen; "
                    "kein abweichender Raumgrundriss erfinden."
                    + pdf_extra
                ),
                "priority": "must",
                "status": "agreed",
            }
        )

    if photos:
        ids = ", ".join(p.get("id") or "?" for p in photos[:4])
        reqs.append(
            {
                "id": "REF-PHOTOS",
                "text": (
                    f"Nutzer-Raumfotos (Assets: {ids}) sind verbindliche visuelle Ground-Truth "
                    "für vorhandene Möbel, Farben, Materialien und Raumcharakter. "
                    "Konzept und Visualisierung müssen denselben Raum zeigen."
                ),
                "priority": "must",
                "status": "agreed",
            }
        )

    if brief and (plans or photos or resolve_reference_asset_ids(state)):
        excerpt = re.sub(r"\s+", " ", brief).strip()[:420]
        reqs.append(
            {
                "id": "REF-VISION",
                "text": (
                    "Aus den Referenzbildern abgeleitete Fakten (verbindlich, soweit sichtbar): "
                    + excerpt
                ),
                "priority": "must",
                "status": "agreed",
            }
        )

    # Wenn IDs da sind, aber Klassifikation nichts gefunden hat: trotzdem Foto-Anker
    if not photos and not plans:
        ids = resolve_reference_asset_ids(state)
        if ids:
            reqs.insert(
                0,
                {
                    "id": "REF-PHOTOS",
                    "text": (
                        f"Nutzer-Referenzanhänge (Assets: {', '.join(ids[:6])}) sind verbindliche "
                        "Ground-Truth für Raum und vorhandene Objekte; Konzept muss dazu passen."
                    ),
                    "priority": "must",
                    "status": "agreed",
                },
            )

    return reqs


def merge_reference_requirements(
    requirements: list[Any] | None,
    anchors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Hängt Referenz-Anker vorne ein; bestehende REF-* werden ersetzt."""
    existing = [r for r in (requirements or []) if isinstance(r, dict)]
    without_ref = [
        r
        for r in existing
        if not str(r.get("id") or "").upper().startswith("REF-")
    ]
    return list(anchors) + without_ref


def reference_ground_truth_block(state: AgentState) -> str:
    """Prompt-Block: Fotos/Grundriss haben Vorrang vor Text-Requirements."""
    classified = classify_reference_assets(state)
    plans = classified["plans"]
    photos = classified["photos"]
    if not plans and not photos and not get_reference_vision_brief(state):
        return ""

    lines = [
        "=== GROUND TRUTH (höchste Priorität) ===",
        "Angehängte Nutzer-Fotos/Grundrisse und ihre Bildanalyse haben Vorrang vor allen "
        "abgeleiteten Text-Requirements. Widersprechen sich VV-Requirements und Fotos, "
        "gelten die Fotos; Requirements nur anpassen, nie den Raum neu erfinden.",
        "",
    ]
    if plans:
        lines.append(
            "Grundriss-Anhänge (verbindlich): "
            + ", ".join(f"{p.get('title') or 'Plan'} [{p.get('id')}]" for p in plans)
        )
        for plan in plans:
            pdf_text = str(plan.get("pdf_text") or "").strip()
            if pdf_text:
                lines.append(f"PDF-Text aus „{plan.get('title')}“:\n{pdf_text[:800]}")
    if photos:
        lines.append(
            "Raumfotos (verbindlich): "
            + ", ".join(f"{p.get('title') or 'Foto'} [{p.get('id')}]" for p in photos[:4])
        )
    brief = get_reference_vision_brief(state)
    if brief:
        lines.append("Referenz-Bildanalyse:\n" + brief)
    lines.append("=== Ende Ground Truth ===")
    return "\n".join(lines)


def load_reference_images_for_llm(state: AgentState, *, limit: int = 4) -> list[tuple[bytes, str]]:
    """Komprimierte JPEG-Bytes für multimodal LLM (max. `limit`)."""
    from app.services.reference_assets import load_reference_image_assets, prepare_image_bytes_for_vision

    ids = resolve_reference_asset_ids(state)
    # Grundrisse zuerst laden, dann Fotos – wichtige Pläne nicht „wegschneiden“
    classified = classify_reference_assets(state, limit=max(limit, 6))
    ordered_ids: list[str] = []
    for group in (classified["plans"], classified["photos"]):
        for row in group:
            rid = str(row.get("id") or "")
            if rid and rid not in ordered_ids:
                ordered_ids.append(rid)
    for rid in ids:
        if rid not in ordered_ids:
            ordered_ids.append(rid)

    assets = load_reference_image_assets(ordered_ids[:limit], limit=limit)
    by_id = {str(a.get("id")): a for a in assets}
    ordered_assets = [by_id[i] for i in ordered_ids if i in by_id][:limit]

    images: list[tuple[bytes, str]] = []
    for asset in ordered_assets:
        path = asset.get("path") or Path(str(asset.get("file_path") or ""))
        if not isinstance(path, Path):
            path = Path(str(path))
        prepared = prepare_image_bytes_for_vision(path)
        if prepared:
            images.append(prepared)
    if ids and not images:
        logger.warning(
            "Referenz-IDs im Prompt (%s), aber keine Bilddateien ladbar",
            ids,
        )
    elif images:
        logger.info("LLM erhält %s Referenzbild(er) aus Chat-Anhängen", len(images))
    return images


def load_concept_images_for_llm(state: AgentState, *, limit: int = 4) -> list[tuple[bytes, str]]:
    """Konzept-Galerie (Raumsituation, Grundriss, Teile) als JPEG-Bytes für Vision-Calls."""
    from app.services.concept_image import concept_image_path, list_concept_gallery
    from app.services.reference_assets import prepare_image_bytes_for_vision

    session_id = str(state.get("session_id") or "anonymous")
    gallery = state.get("concept_image_urls") or list_concept_gallery(session_id)
    images: list[tuple[bytes, str]] = []
    seen: set[str] = set()
    for entry in gallery:
        if len(images) >= limit:
            break
        if not isinstance(entry, dict):
            continue
        view_key = str(entry.get("view_key") or entry.get("kind") or "overview")
        path = concept_image_path(session_id, view=view_key)
        if path is None or not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        prepared = prepare_image_bytes_for_vision(path)
        if prepared:
            images.append(prepared)
    if images:
        logger.info("LLM erhält %s Konzept-Ansicht(en) session=%s", len(images), session_id)
    return images


def load_review_images_for_llm(
    state: AgentState,
    *,
    ref_limit: int = 4,
    concept_limit: int = 3,
) -> tuple[list[tuple[bytes, str]], int, int]:
    """Referenz- + Konzeptbilder für Kohärenz/Jury (Referenzen zuerst)."""
    refs = load_reference_images_for_llm(state, limit=ref_limit)
    concepts = load_concept_images_for_llm(state, limit=concept_limit)
    return refs + concepts, len(refs), len(concepts)


def reference_ids_note(state: AgentState) -> str:
    ids = resolve_reference_asset_ids(state)
    if not ids:
        return ""
    return (
        "\n\nAngehängte Inventar-/Referenz-Asset-IDs (Bilder liegen dem Call ggf. multimodal bei): "
        + ", ".join(ids)
    )


def get_reference_vision_brief(state: AgentState) -> str:
    brief = state.get("reference_vision_brief")
    return str(brief).strip() if brief else ""


def ensure_reference_vision_brief(state: AgentState) -> tuple[str, dict[str, Any]]:
    """Einmalige Bildanalyse (Anthropic bevorzugt) → Textbrief für alle Agenten + Image-Prompts.

    Returns: (brief_text, state_updates)
    """
    existing = get_reference_vision_brief(state)
    if existing:
        return existing, {}

    images = load_reference_images_for_llm(state, limit=5)
    ids = resolve_reference_asset_ids(state)
    updates: dict[str, Any] = {}
    if ids:
        updates["reference_asset_ids"] = ids
    if not images:
        return "", updates

    from agents.llm_client import call_vision_prefer_anthropic

    user_prompt = (
        "Analysiere die angehängten Nutzer-Referenzfotos/Grundrisse.\n"
        f"Asset-IDs: {', '.join(ids) if ids else '(unbekannt)'}\n"
        f"Ausschnitt Nutzeranfrage:\n{(state.get('user_prompt') or '')[:1500]}"
    )
    try:
        brief = call_vision_prefer_anthropic(
            _BRIEF_SYSTEM,
            user_prompt,
            images,
            max_tokens=1200,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Referenz-Vision-Brief fehlgeschlagen: %s", exc)
        brief = ""

    if brief:
        updates["reference_vision_brief"] = brief
        logger.info("Referenz-Vision-Brief erstellt (%s Zeichen)", len(brief))
    return brief, updates


def reference_context_block(state: AgentState) -> str:
    """Textblock für Agent-Prompts: Ground Truth zuerst, dann IDs."""
    gt = reference_ground_truth_block(state).strip()
    if gt:
        return gt
    parts = [reference_ids_note(state).strip()]
    brief = get_reference_vision_brief(state)
    if brief:
        parts.append(
            "Referenz-Bildanalyse (verbindlich, aus angehängten Fotos/Grundriss):\n" + brief
        )
    return "\n\n".join(p for p in parts if p)
