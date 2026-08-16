"""Innenarchitekt – Raumkonzept, Ansichten und optionaler Grundriss.

Läuft vor dem Concept Builder und liefert einen strukturierten Brief:
welche Visualisierungen sinnvoll sind (Gesamtansicht, Teil-Ansichten, Grundriss).
CNC-Teile bleiben Aufgabe des Concept Builders; dieser Agent plant die Umgebung.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_ROOM_RE = re.compile(
    r"zimmer|raum|ecke|wohn|küche|kueche|schlaf|büro|buero|flur|"
    r"einbau|grundriss|innenraum|interior|room|apartment|wohnung",
    re.IGNORECASE,
)

_BRIEF_SYSTEM = """Du bist Innenarchitekt für eine CNC-/Möbel-Werkstatt-App.
Aus der Nutzeranfrage planst du die Raum-Visualisierung (nicht die CNC-Zerspanung).

Antworte NUR mit JSON:
{
  "is_room_concept": bool,
  "need_floorplan": bool,
  "room_summary": string,
  "style_notes": [string, ...],
  "spatial_notes": [string, ...],
  "suggested_views": [
    {
      "kind": "overview" | "part" | "floorplan",
      "label": string,
      "part_name": string | null,
      "focus": string
    }
  ],
  "advice_for_concept": string
}

Regeln:
- Bei Zimmer-/Raum-/Einbau-Anfragen: is_room_concept=true, need_floorplan meist true.
- suggested_views: zuerst eine "overview" (ganzer Raum mit allen Möbeln),
  dann max. 4 "part"-Ansichten (je ein technisches Teil in der Umgebung),
  optional eine "floorplan" (2D-Grundriss von oben, Möbel-Platzierung).
- Bei reinen Einzelteilen ohne Raum: is_room_concept=false, need_floorplan=false,
  nur overview (+ ggf. eine Detailansicht).
- Deutsch in room_summary / focus / advice_for_concept.
- Maximal 6 Einträge in suggested_views (Host-Schutz).
"""


def _looks_like_room(prompt: str) -> bool:
    return bool(_ROOM_RE.search(prompt or ""))


def _heuristic_brief(prompt: str) -> dict[str, Any]:
    room = _looks_like_room(prompt)
    views: list[dict[str, Any]] = [
        {
            "kind": "overview",
            "label": "Raumsituation" if room else "Gesamtansicht",
            "part_name": None,
            "focus": "Fotorealistische Gesamtsituation des gewünschten Möbel-/Raumkonzepts",
        }
    ]
    if room:
        views.append(
            {
                "kind": "floorplan",
                "label": "Grundriss",
                "part_name": None,
                "focus": "2D-Grundriss von oben mit Möbel-Platzierung und Laufwegen",
            }
        )
    return {
        "is_room_concept": room,
        "need_floorplan": room,
        "room_summary": (prompt.strip().split("\n")[0] or "Raumkonzept")[:200],
        "style_notes": ["modern", "wohnlich"] if room else ["werkstatttauglich"],
        "spatial_notes": (
            ["Referenzfotos und Maße der Umgebung priorisieren"]
            if room
            else ["Einzelteil ohne Raumkontext"]
        ),
        "suggested_views": views,
        "advice_for_concept": (
            "Zerlege in CNC-Teile und plane sie so, dass sie in der beschriebenen Umgebung passen."
            if room
            else "Fokussiere auf ein CNC-fertigbares Hauptteil."
        ),
    }


def _enrich_views_from_parts(brief: dict[str, Any], parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Ergänzt Teil-Ansichten; reserviert Slots für Übersicht + Grundriss."""
    need_floor = bool(brief.get("need_floorplan"))
    max_total = 5
    # Slot-Plan: overview + optional floorplan + max. 2 Detail-Ansichten
    # (mehr Teil-Fotos wirken wie konkurrierende Schrank-Varianten)
    reserved = 1 + (1 if need_floor else 0)
    max_parts = min(2, max(0, max_total - reserved))

    overview = {
        "kind": "overview",
        "label": "Raumsituation (maßgeblich)",
        "part_name": None,
        "focus": (
            "Gesamtsituation im Raum – treu zu Nutzer-Referenzfotos; "
            "ein kohärentes Möbelkonzept; Platzierung wie im Foto "
            "(Schrank links von der Ecke, wenn so fotografiert)"
        ),
    }
    floorplan = {
        "kind": "floorplan",
        "label": "Grundriss (aus Nutzerplan)",
        "part_name": None,
        "focus": "Nutzer-Grundriss beibehalten und Möbel-Platzierung ergänzen",
    }

    part_views: list[dict[str, Any]] = []
    for part in parts[:max_parts]:
        name = str(part.get("name") or "").strip()
        if not name:
            continue
        part_views.append(
            {
                "kind": "part",
                "label": f"Teil: {name}",
                "part_name": name,
                "focus": f"„{name}“ eingebaut in derselben Umgebung wie die Referenzfotos",
            }
        )

    views: list[dict[str, Any]] = [overview]
    if need_floor:
        views.append(floorplan)
    views.extend(part_views)
    brief = dict(brief)
    brief["suggested_views"] = views[:max_total]
    brief["need_floorplan"] = need_floor
    return brief


def interior_architect_node(state: AgentState) -> AgentState:
    prompt = state.get("user_prompt") or ""
    guidance = get_agent_guidance("interior_architect")
    advisory = state.get("advisory_notes") or ""

    from agents.reference_images import (
        ensure_reference_vision_brief,
        load_reference_images_for_llm,
        reference_context_block,
        resolve_reference_asset_ids,
    )

    vision_brief, vision_updates = ensure_reference_vision_brief(state)
    state_for_refs = {**state, **vision_updates} if vision_updates else state
    ref_ids = resolve_reference_asset_ids(state_for_refs)
    ref_images = load_reference_images_for_llm(state_for_refs, limit=4)
    user_blob = (
        f"Nutzeranfrage:\n{prompt[:3500]}\n\n"
        f"Flexible-Advisory:\n{str(advisory)[:800]}\n\n"
        f"Guidance:\n{guidance or '(keine)'}\n"
        f"{reference_context_block(state_for_refs)}\n"
    )
    if ref_images:
        user_blob += (
            f"\nEs sind {len(ref_images)} Referenzfoto(s) als Bilder angehängt – "
            "nutze sie als Ground Truth für Raum, Möbel, Farben und Proportionen."
        )

    room_brief: dict[str, Any]
    if is_llm_configured():
        try:
            room_brief = call_llm_json(
                _BRIEF_SYSTEM,
                user_blob,
                max_tokens=1600,
                images=ref_images or None,
            )
            if not isinstance(room_brief, dict) or "suggested_views" not in room_brief:
                room_brief = _heuristic_brief(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Innenarchitekt LLM fehlgeschlagen: %s", exc)
            room_brief = _heuristic_brief(prompt)
    else:
        room_brief = _heuristic_brief(prompt)

    views = room_brief.get("suggested_views") or []
    if not isinstance(views, list) or not views:
        room_brief = _heuristic_brief(prompt)
        views = room_brief["suggested_views"]
    room_brief["suggested_views"] = [v for v in views if isinstance(v, dict)][:5]
    room_brief.setdefault("is_room_concept", _looks_like_room(prompt) or bool(ref_ids))
    # Angehängte Fotos/Grundriss → Grundriss-Ansicht immer mitplanen
    if ref_ids or ref_images or _looks_like_room(prompt):
        room_brief["need_floorplan"] = True
    else:
        room_brief.setdefault("need_floorplan", bool(room_brief.get("is_room_concept")))

    advice = str(room_brief.get("advice_for_concept") or "").strip()
    merged_advisory = advisory
    if advice:
        merged_advisory = f"{advisory}\n\n[Innenarchitekt] {advice}".strip() if advisory else f"[Innenarchitekt] {advice}"

    n_views = len(room_brief["suggested_views"])
    note = (
        f"Raumbrief erstellt ({'Zimmerkonzept' if room_brief.get('is_room_concept') else 'Einzelteil'}): "
        f"{n_views} vorgeschlagene Ansicht(en)"
        + (", inkl. Grundriss" if room_brief.get("need_floorplan") else "")
        + (f", {len(ref_images)} Referenzfoto(s) gesehen" if ref_images else "")
        + "."
    )

    out: dict[str, Any] = {
        "interior_brief": room_brief,
        "interior_consulted": True,
        "advisory_notes": merged_advisory,
        "refinement_request": None,
        "current_agent": "interior_architect",
        "messages": [{"role": "assistant", "content": f"[Innenarchitekt] {note}"}],
        "agent_transcript": [
            make_entry(
                "interior_architect",
                "room_brief",
                note,
                detail={
                    "is_room_concept": room_brief.get("is_room_concept"),
                    "need_floorplan": room_brief.get("need_floorplan"),
                    "room_summary": truncate(str(room_brief.get("room_summary") or ""), 300),
                    "reference_asset_ids": ref_ids,
                    "reference_images_sent": len(ref_images),
                    "reference_vision_brief": truncate(vision_brief, 500) if vision_brief else None,
                    "views": [
                        {"kind": v.get("kind"), "label": v.get("label"), "part_name": v.get("part_name")}
                        for v in room_brief["suggested_views"]
                    ],
                },
                to_agent="supervisor",
            )
        ],
    }
    out.update(vision_updates)
    if ref_ids and not state.get("reference_asset_ids"):
        out["reference_asset_ids"] = ref_ids
    return out


# Re-export für Concept Builder
enrich_interior_views_from_parts = _enrich_views_from_parts
