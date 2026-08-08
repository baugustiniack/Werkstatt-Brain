"""Concept Builder – Requirements-Contract-Synthese & Mehrteil-Dekomposition
(SPEC Kap. 3.2.2, 3.3, 3.5).

Nutzer-Feedback zum Chapter-3-Frontend: die Anfrage kann ein einzelnes
Werkstück ODER mehrere unabhängige Möbelstücke/Bauteile beschreiben (z. B.
ein ganzes Zimmer-Layout). Der Concept Builder zerlegt sie deshalb per
LLM-Call in eine sequenziell abzuarbeitende `parts`-Liste und erzeugt einen
deterministischen 2D-Sketch (`agents/sketch_renderer.py`), der dem Nutzer vor
der eigentlichen 3D-Ausarbeitung zur Freigabe vorgelegt wird
(`escalation_reason="concept_approval"`, siehe `human_escalation.py`).

Ohne konfigurierten Anthropic-Key (`agents/llm_client.is_llm_configured`)
fällt die Dekomposition auf die bisherige Regex-Heuristik zurück (ein
einzelnes Teil), damit das System weiterhin ohne Cloud-API lauffähig bleibt.
"""

import copy
import json
import logging
import re

from agents.llm_client import call_llm_json, is_llm_configured
from agents.sketch_renderer import render_concept_sketch_svg
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

AUTONOMOUS_ADJUSTMENT_FACTOR = 0.8  # -20% Toleranz-Justage, SPEC Kap. 3.5 Beispiel

MATERIAL_KEYWORDS = [
    "multiplex", "sperrholz", "mdf", "fichte", "kiefer", "buche", "eiche",
    "birke", "esche", "nussbaum", "spanplatte", "osb",
]

FEATURE_KEYWORDS = {
    "tasche": "Tasche",
    "pocket": "Tasche",
    "falz": "Falz",
    "durchgangsbohrung": "Durchgangsbohrung",
    "bohrung": "Bohrung",
    "schwalbenschwanz": "Schwalbenschwanz-Zinkung",
    "zinkung": "Schwalbenschwanz-Zinkung",
    "t-nut": "T-Nut",
    "nut": "Nut",
    "fase": "Fase",
    "verrundung": "Verrundung",
}

DIMENSION_PATTERN = re.compile(
    r"(?P<x>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<y>\d+(?:[.,]\d+)?)\s*(?:x|×)\s*(?P<z>\d+(?:[.,]\d+)?)\s*mm",
    re.IGNORECASE,
)
RADIUS_PATTERN = re.compile(r"(?:radius|verrundung)\s*(?:von\s*)?(\d+(?:[.,]\d+)?)\s*mm", re.IGNORECASE)
TOOL_DIAMETER_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm\s*(?:vhm\s*)?(?:fräser|nutfräser)", re.IGNORECASE)
PLATE_THICKNESS_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*mm\s*(?:platte|plattenstärke|dick)", re.IGNORECASE)

_DECOMPOSITION_SYSTEM_PROMPT = """Du bist ein erfahrener Werkstatt-Planer für CNC-gefertigte Holz-Bauteile \
(build123d/CNC-Fräse – KEINE Architektur-, Wand- oder Raum-Modellierung, nur einzelne, unabhängig \
fertigbare Werkstücke aus Plattenmaterial).

Der Nutzer beschreibt einen Wunsch, der EIN Werkstück ODER MEHRERE unabhängige Möbelstücke/Bauteile \
umfassen kann (z.B. ein ganzes Zimmer mit mehreren Möbeln). Zerlege die Anfrage in eine Liste einzelner \
CNC-fertigbarer Teile. Für jedes Teil: leite plausible Abmessungen/Material/Werkzeug ab, auch wenn der \
Nutzer sie nicht explizit nennt (dokumentiere Annahmen in gap_analysis).

Antworte AUSSCHLIESSLICH mit einem einzelnen JSON-Objekt (keine Erklärung, kein Markdown) exakt in \
diesem Format:
{
  "project_title": string,
  "parts": [
    {
      "name": string,
      "functional_geometry": {
        "dimensions_mm": {"x": number, "y": number, "z": number},
        "radii_mm": number or null,
        "wall_thickness_mm": number or null,
        "tolerances_mm": number
      },
      "material_tool_constraints": {
        "material_type": string,
        "plate_thickness_mm": number,
        "tool_diameter_mm": number
      },
      "manufacturing_features": [string, ...],
      "position_xy_mm": {"x": number, "y": number}
    }
  ],
  "gap_analysis": [string, ...]
}

`position_xy_mm` beschreibt die grobe Top-Down-Lage im Raum/Layout (Nullpunkt oben links, in mm) – NUR \
angeben, wenn die Anfrage tatsächlich ein räumliches Mehrteil-Layout beschreibt (z.B. Möbel in einem \
Zimmer); bei einer Einzelteil-Anfrage weglassen."""


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))


def _heuristic_single_part(user_prompt: str) -> dict:
    """Übersetzt den Freitext-Prompt heuristisch (Regex) in EIN Teil – Fallback,
    wenn kein LLM konfiguriert ist oder der Call fehlschlägt."""
    prompt_lower = user_prompt.lower()
    gap_analysis: list[str] = []

    dims_match = DIMENSION_PATTERN.search(user_prompt)
    if dims_match:
        dimensions_mm = {axis: _to_float(dims_match.group(axis)) for axis in ("x", "y", "z")}
    else:
        dimensions_mm = {"x": 100.0, "y": 100.0, "z": 18.0}
        gap_analysis.append("Keine Abmessungen erkannt – Standardmaße 100x100x18mm angenommen.")

    radius_match = RADIUS_PATTERN.search(user_prompt)
    radii_mm = _to_float(radius_match.group(1)) if radius_match else None

    material_type = next((m for m in MATERIAL_KEYWORDS if m in prompt_lower), None)
    if material_type is None:
        material_type = "multiplex"
        gap_analysis.append("Keine Holzart erkannt – Standardmaterial 'Multiplex' angenommen.")

    plate_match = PLATE_THICKNESS_PATTERN.search(user_prompt)
    plate_thickness_mm = _to_float(plate_match.group(1)) if plate_match else dimensions_mm["z"]

    tool_match = TOOL_DIAMETER_PATTERN.search(user_prompt)
    if tool_match:
        tool_diameter_mm = _to_float(tool_match.group(1))
    else:
        tool_diameter_mm = 8.0
        gap_analysis.append("Kein Fräserdurchmesser erkannt – Standardwert 8mm angenommen.")

    manufacturing_features = sorted(
        {label for keyword, label in FEATURE_KEYWORDS.items() if keyword in prompt_lower}
    )
    if not manufacturing_features:
        gap_analysis.append("Keine expliziten Fertigungs-Features erkannt.")

    return {
        "name": "Werkstück",
        "functional_geometry": {
            "dimensions_mm": dimensions_mm,
            "radii_mm": radii_mm,
            "wall_thickness_mm": None,
            "tolerances_mm": 0.2,
        },
        "material_tool_constraints": {
            "material_type": material_type.capitalize(),
            "plate_thickness_mm": plate_thickness_mm,
            "tool_diameter_mm": tool_diameter_mm,
        },
        "manufacturing_features": manufacturing_features,
        "gap_analysis": gap_analysis,
    }


def _fallback_decompose(user_prompt: str) -> dict:
    part = _heuristic_single_part(user_prompt)
    return {"project_title": "Werkstück", "parts": [part], "gap_analysis": list(part["gap_analysis"])}


def _llm_decompose_request(
    user_prompt: str, feedback: str | None, previous_parts: list[dict] | None
) -> dict:
    user_message = f"Nutzeranfrage: {user_prompt}"
    if feedback:
        prev_json = json.dumps({"parts": previous_parts or []}, ensure_ascii=False)
        user_message += (
            f"\n\nBisheriger Entwurf: {prev_json}\n"
            f"Nutzer-Rückmeldung zur Überarbeitung: {feedback}\n"
            "Überarbeite den Entwurf entsprechend dieser Rückmeldung."
        )
    return call_llm_json(_decomposition_system_prompt(), user_message, max_tokens=3000)


def _decomposition_system_prompt() -> str:
    guidance = get_agent_guidance("concept_builder")
    if guidance:
        return f"{_DECOMPOSITION_SYSTEM_PROMPT}\n\nZusätzliche Meta-Coach-Guidance:\n{guidance}"
    return _DECOMPOSITION_SYSTEM_PROMPT


def _build_concept(user_prompt: str, feedback: str | None, previous_parts: list[dict] | None) -> dict:
    if is_llm_configured():
        try:
            result = _llm_decompose_request(user_prompt, feedback, previous_parts)
            parts = result.get("parts") or []
            if not parts:
                raise ValueError("LLM lieferte keine Teile-Liste.")
            for part in parts:
                part.setdefault("name", "Teil")
                part.setdefault("functional_geometry", {})
                part.setdefault("material_tool_constraints", {})
                part.setdefault("manufacturing_features", [])
                part.setdefault("gap_analysis", [])
            return {
                "project_title": result.get("project_title") or "Werkstatt-Projekt",
                "parts": parts,
                "gap_analysis": result.get("gap_analysis", []),
            }
        except Exception as exc:  # noqa: BLE001 - LLM-Ausfall darf den Workflow nie hart stoppen
            logger.warning("LLM-Dekomposition fehlgeschlagen, Fallback auf Heuristik: %s", exc)

    return _fallback_decompose(user_prompt)


def _apply_autonomous_refinement(part: dict, refinement: dict) -> dict:
    """Autonome Justage innerhalb Toleranz vs. dokumentierte Gap (SPEC Kap. 3.5),
    angewendet auf genau EIN Teil (`current_part_index`) der Teile-Liste."""
    updated = copy.deepcopy(part)
    reason = (refinement.get("reason") or "").lower()
    geometry = updated.setdefault("functional_geometry", {})
    gap_analysis = updated.setdefault("gap_analysis", [])

    if refinement.get("reason") == "tool_substitution":
        gap_analysis.append("Werkzeug-Substitution durch Inventory Manager akzeptiert (autonome Justage).")
        return updated

    if any(keyword in reason for keyword in ("radius", "verrundung", "fase", "fillet")):
        current_radius = geometry.get("radii_mm") or 3.0
        new_radius = round(current_radius * AUTONOMOUS_ADJUSTMENT_FACTOR, 2)
        geometry["radii_mm"] = new_radius
        gap_analysis.append(
            f"Autonome Justage: Radius von {current_radius}mm auf {new_radius}mm reduziert "
            f"(Grund: {refinement.get('reason')})."
        )
    else:
        gap_analysis.append(f"Manuelle Überarbeitung angefordert: {refinement.get('reason')}")

    return updated


def concept_builder_node(state: AgentState) -> AgentState:
    refinement = state.get("refinement_request")
    existing_contract = state.get("requirements_contract")
    idx = state.get("current_part_index", 0)
    is_concept_feedback = bool(refinement) and refinement.get("reason") == "concept_feedback"

    if refinement and existing_contract and not is_concept_feedback:
        # Autonome Korrektur EINES Teils (topologischer Konflikt, Werkzeug-Substitution) –
        # der bereits freigegebene Entwurf/Sketch bleibt unverändert, keine Re-Freigabe nötig.
        parts = list(existing_contract.get("parts", []))
        if 0 <= idx < len(parts):
            parts[idx] = _apply_autonomous_refinement(parts[idx], refinement)
        contract = dict(existing_contract)
        contract["parts"] = parts
        note = (
            f"Anforderungskatalog für Teil {idx + 1}/{len(parts)} angepasst "
            f"(Auslöser: {refinement.get('from_agent')})."
        )
        return {
            "requirements_contract": contract,
            "refinement_request": None,
            "generated_code": None,
            "sandbox_result": None,
            "current_agent": "concept_builder",
            "messages": [{"role": "assistant", "content": f"[Concept Builder] {note}"}],
            "agent_transcript": [
                make_entry(
                    "concept_builder",
                    "part_refinement",
                    note,
                    detail={"part_index": idx, "refinement": refinement, "part": parts[idx] if 0 <= idx < len(parts) else None},
                    to_agent="inventory_manager",
                )
            ],
        }

    # Initiale Erstellung ODER Überarbeitung nach Nutzer-Feedback auf den 2D-Entwurf.
    feedback_text = refinement.get("feedback") if is_concept_feedback else None
    previous_parts = existing_contract.get("parts") if (is_concept_feedback and existing_contract) else None

    concept = _build_concept(state["user_prompt"], feedback_text, previous_parts)
    sketch_svg = render_concept_sketch_svg(concept["project_title"], concept["parts"])

    contract = {
        "project_title": concept["project_title"],
        "raw_prompt": state["user_prompt"],
        "parts": concept["parts"],
        "gap_analysis": concept.get("gap_analysis", []),
    }

    concept_image_url = None
    try:
        from app.services.concept_image import generate_concept_image

        concept_image_url = generate_concept_image(
            session_id=state.get("session_id") or "anonymous",
            project_title=concept["project_title"],
            user_prompt=state["user_prompt"],
            parts=concept["parts"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Konzept-Foto übersprungen: %s", exc)

    verb = "überarbeitet" if is_concept_feedback else "erstellt"
    photo_note = " Raumfoto erzeugt." if concept_image_url else " (kein Foto – OpenAI-Key fehlt oder Generierung fehlgeschlagen)."
    note = (
        f"Entwurf {verb}: {concept['project_title']} ({len(concept['parts'])} Teil(e))."
        f"{photo_note} Freigabe erforderlich."
    )

    return {
        "requirements_contract": contract,
        "concept_sketch_svg": sketch_svg,
        "concept_image_url": concept_image_url,
        "concept_approved": False,
        "current_part_index": 0,
        "completed_parts": [],
        "refinement_request": None,
        "generated_code": None,
        "sandbox_result": None,
        "human_approval_required": True,
        "escalation_reason": "concept_approval",
        "current_agent": "concept_builder",
        "messages": [{"role": "assistant", "content": f"[Concept Builder] {note}"}],
        "agent_transcript": [
            make_entry(
                "concept_builder",
                "concept_draft",
                note,
                detail={
                    "project_title": concept["project_title"],
                    "parts_count": len(concept["parts"]),
                    "part_names": [p.get("name") for p in concept["parts"]],
                    "gap_analysis": concept.get("gap_analysis", []),
                    "concept_image_url": concept_image_url,
                    "llm_used": is_llm_configured(),
                    "user_prompt": truncate(state.get("user_prompt"), 500),
                    "feedback": truncate(feedback_text, 500) if feedback_text else None,
                },
                to_agent="human_escalation",
            )
        ],
    }
