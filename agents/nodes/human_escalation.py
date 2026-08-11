"""Human Escalation – Konzept- und Requirements-Freigabe (interrupt).

Requirements-Flow:
1) Eine Klärungsfrage nach der anderen (`requirements_question`)
2) Finale Anforderungsliste bestätigen/anpassen (`requirements_confirm`)

Andere Eskalationsgründe werden ohne User-Pause fortgesetzt.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from agents.nodes.vv_manager import finalize_requirements_with_answers, revise_requirements_with_feedback
from agents.state import AgentState
from agents.transcript import make_entry, truncate

logger = logging.getLogger(__name__)


def _decision_is_revise(decision: object) -> bool:
    if not isinstance(decision, dict):
        return False
    if decision.get("decision") == "revise":
        return True
    if decision.get("approved") is False:
        return True
    return False


def _decision_is_approve(decision: object) -> bool:
    if not isinstance(decision, dict):
        return False
    if decision.get("decision") == "approve":
        return True
    if decision.get("approved") is True:
        return True
    return False


def _decision_feedback(decision: object) -> str:
    if not isinstance(decision, dict):
        return ""
    return str(decision.get("feedback") or decision.get("note") or decision.get("answer") or "")


def _decision_answer(decision: object) -> str:
    if not isinstance(decision, dict):
        return ""
    return str(decision.get("answer") or decision.get("feedback") or decision.get("note") or "").strip()


def _handle_concept_approval(state: AgentState) -> AgentState:
    payload = {
        "reason": "concept_approval",
        "requirements_contract": state.get("requirements_contract"),
        "concept_sketch_svg": state.get("concept_sketch_svg"),
        "concept_image_url": state.get("concept_image_url"),
        "vv_requirements": state.get("vv_requirements"),
        "iteration_count": state.get("iteration_count", 0),
    }

    logger.info("Human Escalation: Entwurf pausiert zur Freigabe.")
    decision = interrupt(payload)
    logger.info("Human Escalation: Entwurfs-Entscheidung: %s", decision)

    if _decision_is_revise(decision):
        feedback = _decision_feedback(decision)
        return {
            "human_approval_required": False,
            "escalation_reason": None,
            "iteration_count": 0,
            "current_agent": "human_escalation",
            "refinement_request": {
                "from_agent": "human",
                "target": "concept_builder",
                "reason": "concept_feedback",
                "feedback": feedback,
            },
            "messages": [
                {"role": "system", "content": "[Human Escalation] Nutzer fordert Überarbeitung des Entwurfs an."}
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "concept_revise",
                    "Nutzer: Entwurf überarbeiten",
                    detail={"feedback": truncate(feedback, 800)},
                    to_agent="concept_builder",
                )
            ],
        }

    return {
        "human_approval_required": False,
        "escalation_reason": None,
        "iteration_count": 0,
        "concept_approved": True,
        "current_agent": "human_escalation",
        "messages": [{"role": "system", "content": "[Human Escalation] Entwurf freigegeben – Ausarbeitung startet."}],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "concept_approved",
                "Nutzer: Entwurf freigegeben → Ausarbeitung",
                detail={"decision": decision},
                to_agent="supervisor",
            )
        ],
    }


def _handle_requirements_approval(state: AgentState) -> AgentState:
    """Fragen nacheinander stellen, dann finale Anforderungsliste bestätigen lassen."""
    vv = dict(state.get("vv_requirements") or {}) if isinstance(state.get("vv_requirements"), dict) else {}
    phase = str(vv.get("phase") or state.get("vv_phase") or "concept")
    questions = [str(q).strip() for q in (vv.get("open_questions") or []) if str(q).strip()]
    answers: list[dict[str, Any]] = list(state.get("vv_qa_answers") or [])

    logger.info(
        "Human Escalation: Requirements-Interview (%s Fragen, %s bereits beantwortet).",
        len(questions),
        len(answers),
    )

    # 1) Eine Frage nach der anderen
    for idx in range(len(answers), len(questions)):
        question = questions[idx]
        payload = {
            "reason": "requirements_question",
            "question": question,
            "question_index": idx,
            "question_total": len(questions),
            "answered_so_far": answers,
            "draft_title": vv.get("title"),
            "draft_summary": vv.get("summary"),
            "phase": phase,
            "vv_requirements": vv,
            "iteration_count": state.get("iteration_count", 0),
        }
        decision = interrupt(payload)
        answer = _decision_answer(decision)
        if not answer and _decision_is_revise(decision):
            # „Überspringen“ / leere Antwort erlauben mit Hinweis
            answer = "(keine Angabe)"
        if not answer:
            answer = "(keine Angabe)"
        answers.append({"question": question, "answer": answer})
        logger.info("V&V Q%d/%d beantwortet.", idx + 1, len(questions))

    # 2) Finale Liste erstellen
    prompt = state.get("user_prompt") or ""
    final_vv = finalize_requirements_with_answers(
        prompt=prompt,
        draft=vv,
        answers=answers,
        phase=phase,
    )

    # 3) Bestätigungsschleife
    while True:
        confirm_payload = {
            "reason": "requirements_confirm",
            "vv_requirements": final_vv,
            "qa_answers": answers,
            "summary": final_vv.get("summary"),
            "phase": final_vv.get("phase") or phase,
            "iteration_count": state.get("iteration_count", 0),
            "requirements_contract": state.get("requirements_contract"),
        }
        decision = interrupt(confirm_payload)
        logger.info("Human Escalation: Requirements-Bestätigung: %s", decision)

        if _decision_is_approve(decision):
            consulted = list(state.get("vv_consulted_phases") or [])
            if phase not in consulted:
                consulted.append(phase)
            return {
                "human_approval_required": False,
                "escalation_reason": None,
                "vv_needs_alignment": False,
                "vv_approved": True,
                "vv_requirements": final_vv,
                "vv_phase": final_vv.get("phase") or phase,
                "vv_qa_answers": answers,
                "vv_consulted_phases": consulted,
                "iteration_count": 0,
                "refinement_request": None,
                "current_agent": "human_escalation",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"[Human Escalation] Anforderungsliste bestätigt "
                            f"({len(final_vv.get('requirements') or [])} Punkte, "
                            f"{len(answers)} Antwort(en))."
                        ),
                    }
                ],
                "agent_transcript": [
                    make_entry(
                        "human_escalation",
                        "requirements_approved",
                        "Nutzer: Anforderungsliste freigegeben",
                        detail={
                            "phase": phase,
                            "requirements_count": len(final_vv.get("requirements") or []),
                            "answers_count": len(answers),
                        },
                        to_agent="supervisor",
                    )
                ],
            }

        # Anpassen → Liste überarbeiten und erneut bestätigen
        feedback = _decision_feedback(decision)
        if not feedback:
            feedback = "Bitte Anforderungen klarer und vollständiger formulieren."
        final_vv = revise_requirements_with_feedback(
            current=final_vv,
            feedback=feedback,
            answers=answers,
        )


def _auto_continue(state: AgentState) -> AgentState:
    """Generic-Eskalationen: Flags löschen und Pipeline fortsetzen."""
    reason = state.get("escalation_reason") or "unbekannt"
    logger.warning(
        "Human Escalation übersprungen (kein User-Interrupt): %s – Agenten arbeiten weiter.",
        reason,
    )
    updates: AgentState = {
        "human_approval_required": False,
        "escalation_reason": None,
        "current_agent": "human_escalation",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"[Human Escalation] Keine User-Pause ({reason}). "
                    "Pipeline setzt die Arbeit fort."
                ),
            }
        ],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "auto_continue",
                f"Eskalation '{reason}' übersprungen – weiter ohne User",
                detail={"reason": reason, "iteration_count": state.get("iteration_count", 0)},
                to_agent="supervisor",
            )
        ],
    }

    sandbox_result = state.get("sandbox_result")
    if sandbox_result and sandbox_result.get("status") != "SUCCESS":
        updates["generated_code"] = None
        updates["sandbox_result"] = None
        updates["refinement_request"] = {
            "from_agent": "human_escalation",
            "target": "builder_3d",
            "reason": f"auto_continue_after:{reason}",
        }
    else:
        stock_context = state.get("stock_and_tool_context") or {}
        recommended_tools = stock_context.get("data", {}).get("recommended_tools")
        if not recommended_tools:
            updates["stock_and_tool_context"] = None

    return updates


def human_escalation_node(state: AgentState) -> AgentState:
    """Pausiert bei Konzept- oder Requirements-Freigabe; sonst Auto-Continue."""

    if state.get("escalation_reason") == "concept_approval":
        return _handle_concept_approval(state)
    if state.get("escalation_reason") == "requirements_approval":
        return _handle_requirements_approval(state)

    return _auto_continue(state)
