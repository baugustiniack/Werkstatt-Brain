"""Konzept-Jury-Reviewer – ein Jury-Mitglied bewertet das aktuelle Konzept."""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.concept_panel import (
    clamp_grade,
    grade_label,
    next_reviewer_updates,
    pop_reviewer,
    role_focus,
)
from agents.design_spec import load_design_spec, validate_design_spec
from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate

logger = logging.getLogger(__name__)

_SYSTEM = """Du bist Jury-Mitglied eines CNC-/Möbel-Konzept-Konsenses.
Vergib eine Schulnote 1–6 (1=sehr gut … 5=mangelhaft … 6=ungenügend) für das vorgestellte Konzept.
Antworte NUR mit JSON:
{
  "grade": 1|2|3|4|5|6,
  "verdict": string,
  "strengths": [string, ...],
  "issues": [string, ...],
  "improvement": string
}
Regeln:
- Sei streng, aber fair; Mangelhaft (5+) nur bei echten Blockern (Maßbruch, Spec-Fehler, Raumkonflikt).
- improvement: eine konkrete Korrekturanweisung für den Concept Builder.
- Fokus deiner Rolle wird im User-Prompt genannt – bewerte primär aus dieser Perspektive.
"""


def _contract_digest(contract: dict[str, Any] | None) -> str:
    if not isinstance(contract, dict):
        return "(kein Contract)"
    parts = contract.get("parts") or []
    lines = [f"Titel: {contract.get('project_title')}", f"Teile: {len(parts)}"]
    for i, p in enumerate(parts[:10]):
        if not isinstance(p, dict):
            continue
        geom = p.get("functional_geometry") if isinstance(p.get("functional_geometry"), dict) else {}
        dims = geom.get("dimensions_mm") if isinstance(geom.get("dimensions_mm"), dict) else {}
        lines.append(
            f"  {i + 1}. {p.get('name')}: "
            f"{dims.get('x')}×{dims.get('y')}×{dims.get('z')} mm"
        )
    return "\n".join(lines)


def _heuristic_grade(state: AgentState, agent_id: str) -> dict[str, Any]:
    spec = load_design_spec(state.get("design_spec"))
    errors: list[str] = []
    warnings: list[str] = []
    if spec:
        spec = validate_design_spec(spec)
        errors = list(spec.validation_errors or [])
        warnings = list(spec.validation_warnings or [])
    if errors:
        grade = 5
        verdict = "Design Spec blockiert – Konzept mangelhaft."
        improvement = "; ".join(errors[:3])
    elif warnings:
        grade = 3
        verdict = "Konzept brauchbar, aber mit Restrisiken."
        improvement = "; ".join(warnings[:3])
    elif state.get("concept_image_url") or state.get("concept_image_urls"):
        grade = 2
        verdict = "Konzept mit Galerie und Contract wirkt stimmig (Heuristik)."
        improvement = "Details und Raumtreue nochmals gegen Referenzfotos prüfen."
    else:
        grade = 4
        verdict = "Kein Konzeptfoto – Bewertung unsicher."
        improvement = "Konzeptgalerie erzeugen und Maße an Spec binden."
    return {
        "agent_id": agent_id,
        "grade": grade,
        "verdict": verdict,
        "strengths": [],
        "issues": errors or warnings,
        "improvement": improvement,
    }


def concept_panel_reviewer_node(state: AgentState) -> AgentState:
    agent_id = str(state.get("panel_reviewer_id") or "").strip()
    queue = [str(x) for x in (state.get("concept_panel_queue") or []) if str(x).strip()]
    if not agent_id and queue:
        agent_id = queue[0]
    if not agent_id:
        logger.warning("concept_panel_reviewer ohne panel_reviewer_id – skip")
        return {
            "current_agent": "concept_panel_reviewer",
            "messages": [{"role": "system", "content": "[Jury] Kein Reviewer gesetzt – übersprungen."}],
            "agent_transcript": [
                make_entry(
                    "concept_panel_reviewer",
                    "panel_skip",
                    "Kein panel_reviewer_id",
                    to_agent="supervisor",
                )
            ],
        }

    contract = state.get("requirements_contract") if isinstance(state.get("requirements_contract"), dict) else {}
    spec = load_design_spec(state.get("design_spec"))
    spec_block = spec.as_prompt_block() if spec else "(keine Design Spec)"
    focus = role_focus(agent_id)

    grade_row: dict[str, Any]
    if is_llm_configured():
        try:
            user = (
                f"Deine Jury-Rolle: {agent_id}\n"
                f"Fokus: {focus}\n\n"
                f"Contract:\n{_contract_digest(contract)}\n\n"
                f"{spec_block}\n\n"
                f"Vision-Brief:\n{truncate(str(state.get('reference_vision_brief') or ''), 1200)}\n\n"
                f"Galerie-Bilder: {len(state.get('concept_image_urls') or [])} "
                f"(primary={bool(state.get('concept_image_url'))})\n"
                f"User-Prompt:\n{truncate(str(state.get('user_prompt') or ''), 1500)}\n"
            )
            result = call_llm_json(_SYSTEM, user, max_tokens=900)
            if not isinstance(result, dict):
                raise ValueError("kein JSON-Objekt")
            grade_row = {
                "agent_id": agent_id,
                "grade": clamp_grade(result.get("grade")),
                "verdict": str(result.get("verdict") or "").strip() or grade_label(result.get("grade")),
                "strengths": [str(x) for x in (result.get("strengths") or [])[:5]],
                "issues": [str(x) for x in (result.get("issues") or [])[:6]],
                "improvement": str(result.get("improvement") or "").strip(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jury-LLM (%s) fehlgeschlagen: %s", agent_id, exc)
            grade_row = _heuristic_grade(state, agent_id)
    else:
        grade_row = _heuristic_grade(state, agent_id)

    grades = [g for g in (state.get("concept_panel_grades") or []) if isinstance(g, dict)]
    # Ersetzen falls derselbe Agent in der Runde schon bewertet hat
    grades = [g for g in grades if g.get("agent_id") != agent_id]
    grades.append(grade_row)

    remaining = pop_reviewer(queue, agent_id)
    nxt = next_reviewer_updates(remaining)
    note = (
        f"Jury {agent_id}: Note {grade_row['grade']} ({grade_label(grade_row['grade'])}) – "
        f"{truncate(grade_row.get('verdict'), 160)}"
    )
    logger.info(
        "Jury-Runde %s | %s → Note %s | Queue rest=%s",
        state.get("concept_panel_round"),
        agent_id,
        grade_row["grade"],
        remaining,
    )

    return {
        "concept_panel_grades": grades,
        "concept_panel_queue": nxt["concept_panel_queue"],
        "panel_reviewer_id": nxt["panel_reviewer_id"],
        "current_agent": "concept_panel_reviewer",
        "messages": [{"role": "assistant", "content": f"[Jury] {note}"}],
        "agent_transcript": [
            make_entry(
                "concept_panel_reviewer",
                "panel_grade",
                note,
                detail={
                    "agent_id": agent_id,
                    "grade": grade_row["grade"],
                    "verdict": grade_row.get("verdict"),
                    "issues": grade_row.get("issues"),
                    "improvement": truncate(grade_row.get("improvement"), 400),
                    "round": state.get("concept_panel_round"),
                    "queue_remaining": remaining,
                },
                to_agent="supervisor",
            )
        ],
    }
