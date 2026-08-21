"""Konzept-Jury-Reviewer – ein Jury-Mitglied bewertet das aktuelle Konzept."""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.concept_panel import (
    MANGELHAFT_MIN,
    clamp_grade,
    grade_label,
    last_snapshot,
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
- Sei streng und konsistent: Wenn sich Konzept und Raumtreue nicht verbessert haben, dürfen Noten
  gegenüber der Vor-Runde NICHT besser werden ohne explizite Begründung.
- Mangelhaft (5+) bei echten Blockern (Maßbruch, Spec-Fehler, Raumkonflikt, Grundriss ignoriert).
- improvement: eine konkrete Korrekturanweisung für den Concept Builder.
- Fokus deiner Rolle wird im User-Prompt genannt – bewerte primär aus dieser Perspektive.
- Referenzfotos/Grundriss und Konzeptbilder sind multimodal beigefügt – prüfe visuell.
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


def _prior_round_block(state: AgentState) -> str:
    snap = last_snapshot(state)
    if not snap:
        return ""
    lines = [
        f"Vorherige Jury-Runde {snap.get('round')}: Schnitt {snap.get('average')}",
        "Noten der Vor-Runde (Referenz – nicht ohne sichtbare Verbesserung besser bewerten):",
    ]
    for g in snap.get("grades") or []:
        if not isinstance(g, dict):
            continue
        aid = str(g.get("agent_id") or "?")
        grade = clamp_grade(g.get("grade"))
        lines.append(f"- {aid}: Note {grade} ({grade_label(grade)})")
        verdict = str(g.get("verdict") or "").strip()
        if verdict:
            lines.append(f"  Urteil: {verdict}")
        for issue in (g.get("issues") or [])[:3]:
            lines.append(f"  Problem: {issue}")
    feedback = str(state.get("last_concept_feedback") or "").strip()
    if feedback:
        lines.append("")
        lines.append("Nutzer-/Jury-Feedback für diese Überarbeitung:")
        lines.append(truncate(feedback, 2000))
    return "\n".join(lines)


def _coherence_block(state: AgentState) -> str:
    critique = state.get("coherence_critique")
    if not isinstance(critique, dict):
        return ""
    parts: list[str] = ["Kohärenz-Prüfung (Bild↔Text↔Konzept):"]
    if critique.get("summary"):
        parts.append(str(critique["summary"]))
    for label, key in (
        ("Widersprüche", "contradictions"),
        ("Konzept-Mängel", "concept_issues"),
        ("Logiklücken", "logic_gaps"),
    ):
        items = critique.get(key) or []
        if items:
            parts.append(f"{label}: " + "; ".join(str(x) for x in items[:4]))
    severity = critique.get("severity")
    if severity:
        parts.append(f"Schwere: {severity}")
    return "\n".join(parts)


def _load_jury_images(state: AgentState, *, limit: int = 5) -> list[tuple[bytes, str]]:
    from agents.reference_images import load_review_images_for_llm

    images, _, _ = load_review_images_for_llm(state, ref_limit=3, concept_limit=limit - 3)
    return images[:limit]


def _heuristic_grade(state: AgentState, agent_id: str) -> dict[str, Any]:
    spec = load_design_spec(state.get("design_spec"))
    errors: list[str] = []
    warnings: list[str] = []
    if spec:
        spec = validate_design_spec(spec)
        errors = list(spec.validation_errors or [])
        warnings = list(spec.validation_warnings or [])

    critique = state.get("coherence_critique") if isinstance(state.get("coherence_critique"), dict) else {}
    critique_issues = (
        list(critique.get("concept_issues") or [])
        + list(critique.get("contradictions") or [])
        + list(critique.get("logic_gaps") or [])
    )

    prior = last_snapshot(state)
    prior_grade: int | None = None
    if prior:
        for g in prior.get("grades") or []:
            if isinstance(g, dict) and g.get("agent_id") == agent_id:
                prior_grade = clamp_grade(g.get("grade"))
                break

    if errors or str(critique.get("severity") or "").lower() in {"high", "critical", "schwer"}:
        grade = 5
        verdict = "Design Spec oder Kohärenz blockiert – Konzept mangelhaft."
        improvement = "; ".join((errors or critique_issues)[:3]) or "Spec-Fehler beheben."
    elif critique_issues:
        grade = max(4, prior_grade or 4)
        verdict = "Kohärenz-Probleme – Konzept noch nicht raumtreu genug."
        improvement = "; ".join(str(x) for x in critique_issues[:3])
    elif warnings:
        grade = max(3, prior_grade or 3)
        verdict = "Konzept brauchbar, aber mit Restrisiken."
        improvement = "; ".join(warnings[:3])
    elif state.get("concept_image_url") or state.get("concept_image_urls"):
        grade = 3 if prior_grade and prior_grade >= 4 else 2
        if prior_grade is not None:
            grade = max(grade, prior_grade)
        verdict = "Konzept mit Galerie (Heuristik – visuelle Prüfung empfohlen)."
        improvement = "Raumtreue gegen Referenzfotos/Grundriss verifizieren."
    else:
        grade = 4
        verdict = "Kein Konzeptfoto – Bewertung unsicher."
        improvement = "Konzeptgalerie erzeugen und Maße an Spec binden."

    return {
        "agent_id": agent_id,
        "grade": grade,
        "verdict": verdict,
        "strengths": [],
        "issues": errors or warnings or critique_issues,
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
    prior_block = _prior_round_block(state)
    coherence_block = _coherence_block(state)
    jury_images = _load_jury_images(state)

    grade_row: dict[str, Any]
    if is_llm_configured():
        try:
            user = (
                f"Deine Jury-Rolle: {agent_id}\n"
                f"Fokus: {focus}\n\n"
                f"Aktuelle Jury-Runde: {state.get('concept_panel_round')}\n"
                f"Konzept-Revision nach Nutzer-Feedback: {bool(state.get('concept_revision'))}\n\n"
                f"Contract:\n{_contract_digest(contract)}\n\n"
                f"{spec_block}\n\n"
            )
            if prior_block:
                user += f"{prior_block}\n\n"
            if coherence_block:
                user += f"{coherence_block}\n\n"
            user += (
                f"Vision-Brief:\n{truncate(str(state.get('reference_vision_brief') or ''), 1200)}\n\n"
                f"Galerie-Bilder: {len(state.get('concept_image_urls') or [])} "
                f"(primary={bool(state.get('concept_image_url'))})\n"
                f"Multimodale Bilder beigefügt: {len(jury_images)} "
                f"(Referenz + Konzept)\n"
                f"User-Prompt:\n{truncate(str(state.get('user_prompt') or ''), 1500)}\n"
            )
            result = call_llm_json(_SYSTEM, user, max_tokens=900, images=jury_images or None)
            if not isinstance(result, dict):
                raise ValueError("kein JSON-Objekt")
            grade = clamp_grade(result.get("grade"))
            snap = last_snapshot(state)
            if snap:
                for g in snap.get("grades") or []:
                    if isinstance(g, dict) and g.get("agent_id") == agent_id:
                        prior_g = clamp_grade(g.get("grade"))
                        remaining = list(result.get("issues") or [])
                        critique = state.get("coherence_critique")
                        if isinstance(critique, dict):
                            remaining.extend(critique.get("concept_issues") or [])
                            remaining.extend(critique.get("contradictions") or [])
                        if grade < prior_g and (remaining or prior_g >= MANGELHAFT_MIN):
                            grade = prior_g
                        break
            grade_row = {
                "agent_id": agent_id,
                "grade": grade,
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
    grades = [g for g in grades if g.get("agent_id") != agent_id]
    grades.append(grade_row)

    remaining = pop_reviewer(queue, agent_id)
    nxt = next_reviewer_updates(remaining)
    note = (
        f"Jury {agent_id}: Note {grade_row['grade']} ({grade_label(grade_row['grade'])}) – "
        f"{truncate(grade_row.get('verdict'), 160)}"
    )
    logger.info(
        "Jury-Runde %s | %s → Note %s | Queue rest=%s | Bilder=%s",
        state.get("concept_panel_round"),
        agent_id,
        grade_row["grade"],
        remaining,
        len(jury_images),
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
                    "images_sent": len(jury_images),
                },
                to_agent="supervisor",
            )
        ],
    }
