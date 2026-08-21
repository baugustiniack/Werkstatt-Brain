"""Human Escalation – Konzept- und Requirements-Freigabe (interrupt).

Wichtig (LangGraph): Nach jedem `interrupt()`-Resume startet der Node von vorn.
Deshalb: maximal EIN Interrupt pro Aufruf, Zwischenstand immer in den State schreiben.
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
    if _decision_is_next_round(decision):
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


def _decision_user_grade(decision: object) -> int | None:
    if not isinstance(decision, dict):
        return None
    raw = decision.get("user_grade")
    if raw is None:
        return None
    try:
        g = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    if 1 <= g <= 6:
        return g
    return None


def _decision_is_next_round(decision: object) -> bool:
    if not isinstance(decision, dict):
        return False
    d = str(decision.get("decision") or "").strip().lower()
    return d in {"revise", "next_round"}


def _decision_answer(decision: object) -> str:
    if not isinstance(decision, dict):
        return ""
    return str(decision.get("answer") or decision.get("feedback") or decision.get("note") or "").strip()


def _decision_is_skip(decision: object) -> bool:
    if not isinstance(decision, dict):
        return False
    return str(decision.get("decision") or "").strip().lower() == "skip"


def _interview_answer(decision: object) -> str:
    """Antwort oder explizites Überspringen (kein Constraint)."""
    from agents.design_spec import SKIP_ANSWER_CANONICAL, is_skip_answer

    if _decision_is_skip(decision):
        return SKIP_ANSWER_CANONICAL
    raw = _decision_answer(decision)
    if is_skip_answer(raw):
        return SKIP_ANSWER_CANONICAL
    return raw


def _answered_keys(answers: list[dict[str, Any]]) -> set[str]:
    return {
        str(a.get("question") or "").strip().lower()
        for a in answers
        if isinstance(a, dict) and str(a.get("question") or "").strip()
    }


def _pending_questions(questions: list[str], answers: list[dict[str, Any]]) -> list[str]:
    """Offene Fragen streng nach Antwort-Index (kein Text-Match → kein Doppel-Fragen)."""
    cleaned = [q.strip() for q in questions if str(q).strip()]
    idx = len(answers)
    if idx >= len(cleaned):
        return []
    return cleaned[idx:]


def _handle_concept_approval(state: AgentState) -> AgentState:
    session_id = str(state.get("session_id") or "anonymous")
    try:
        from app.services.concept_image import ensure_gallery_urls

        gallery = ensure_gallery_urls(session_id, state.get("concept_image_urls") or [])
    except Exception:  # noqa: BLE001
        gallery = list(state.get("concept_image_urls") or [])

    primary = state.get("concept_image_url") or (gallery[0]["url"] if gallery else None)
    payload = {
        "reason": "concept_approval",
        "session_id": session_id,
        "requirements_contract": state.get("requirements_contract"),
        "concept_sketch_svg": state.get("concept_sketch_svg"),
        "concept_image_url": primary,
        "concept_image_urls": gallery,
        "reference_asset_ids": state.get("reference_asset_ids") or [],
        "coherence_critique": state.get("coherence_critique"),
        "vv_requirements": state.get("vv_requirements"),
        "concept_panel_grades": state.get("concept_panel_grades") or [],
        "concept_panel_average": state.get("concept_panel_average"),
        "concept_panel_passed": bool(state.get("concept_panel_passed")),
        "concept_panel_forced": bool(state.get("concept_panel_forced")),
        "concept_panel_reverted": bool(state.get("concept_panel_reverted")),
        "concept_panel_round": state.get("concept_panel_round"),
        "concept_complexity": state.get("concept_complexity"),
        "concept_roster": state.get("concept_roster") or [],
        "concept_complexity_reasons": state.get("concept_complexity_reasons") or [],
        "iteration_count": state.get("iteration_count", 0),
    }
    logger.info(
        "Human Escalation: Konzept-Freigabe mit %s Galerie-Bild(ern)",
        len(payload["concept_image_urls"] or []),
    )
    decision = interrupt(payload)

    if _decision_is_revise(decision):
        from agents.concept_panel import MAX_PANEL_ROUNDS, build_revision_feedback, grade_label

        feedback = _decision_feedback(decision).strip()
        user_grade = _decision_user_grade(decision)
        grades = list(state.get("concept_panel_grades") or [])
        round_no = int(state.get("concept_panel_round") or 0)
        jury_block = build_revision_feedback(
            grades,
            average=state.get("concept_panel_average")
            if isinstance(state.get("concept_panel_average"), (int, float))
            else None,
            max_rounds_reached=round_no >= MAX_PANEL_ROUNDS,
        )
        parts: list[str] = []
        if user_grade is not None:
            parts.append(
                f"Nutzer-Bewertung: {user_grade}/6 ({grade_label(user_grade)})."
            )
        if feedback:
            parts.append(f"Nutzer-Feedback:\n{feedback}")
        parts.append(f"Jury-Feedback (Runde {state.get('concept_panel_round') or '?'}):\n{jury_block}")
        combined = "\n\n".join(parts)

        history = list(state.get("concept_panel_history") or [])
        return {
            "human_approval_required": False,
            "escalation_reason": None,
            "iteration_count": 0,
            "concept_critiqued": False,
            "concept_open_points_cleared": False,
            "concept_panel_done": False,
            "concept_panel_passed": False,
            "concept_panel_forced": False,
            "concept_panel_awaiting_rebuild": True,
            "concept_panel_queue": [],
            "concept_panel_grades": [],
            "concept_panel_average": None,
            "concept_panel_history": history,
            "concept_panel_reverted": False,
            "panel_reviewer_id": None,
            "last_user_grade": user_grade,
            "coherence_critique": None,
            "current_agent": "human_escalation",
            "refinement_request": {
                "from_agent": "human",
                "target": "concept_builder",
                "reason": "concept_feedback",
                "feedback": combined,
            },
            "messages": [
                {"role": "system", "content": "[Human Escalation] Nutzer startet nächste Konzept-Runde."}
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "concept_next_round",
                    "Nutzer: nächste Konzept-Runde",
                    detail={
                        "feedback": truncate(feedback, 800),
                        "user_grade": user_grade,
                        "panel_round": state.get("concept_panel_round"),
                    },
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
    """Eine Requirements-Frage pro Aufruf; danach Confirm-Stage."""
    vv = dict(state.get("vv_requirements") or {}) if isinstance(state.get("vv_requirements"), dict) else {}
    phase = str(vv.get("phase") or state.get("vv_phase") or "concept")
    questions = [str(q).strip() for q in (vv.get("open_questions") or []) if str(q).strip()]
    answers: list[dict[str, Any]] = list(state.get("vv_qa_answers") or [])
    pending = _pending_questions(questions, answers)

    logger.info(
        "Human Escalation: Requirements-Interview (%s offen / %s gesamt, %s beantwortet).",
        len(pending),
        len(questions),
        len(answers),
    )

    if pending:
        question = pending[0]
        idx = len(answers)
        payload = {
            "reason": "requirements_question",
            "question": question,
            "question_index": idx,
            "question_total": len(questions),
            "answered_so_far": answers,
            "draft_title": vv.get("title"),
            "draft_summary": vv.get("summary"),
            "phase": phase,
            "vv_requirements": {
                "title": vv.get("title"),
                "phase": phase,
                "summary": vv.get("summary"),
                "requirements": (vv.get("requirements") or [])[:12],
            },
            "iteration_count": state.get("iteration_count", 0),
        }
        decision = interrupt(payload)
        answer = _interview_answer(decision)
        if not answer:
            answer = "(keine Angabe)"

        answers = [*answers, {"question": question, "answer": answer}]
        still = _pending_questions(questions, answers)
        logger.info("V&V Q%d/%d beantwortet (%s offen).", len(answers), len(questions), len(still))

        if still:
            return {
                "vv_qa_answers": answers,
                "vv_last_rejected_answer": None,
                "human_approval_required": True,
                "escalation_reason": "requirements_approval",
                "vv_needs_alignment": True,
                "current_agent": "human_escalation",
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"[Human Escalation] V&V-Antwort {len(answers)}/{len(questions)} erfasst.",
                    }
                ],
                "agent_transcript": [
                    make_entry(
                        "human_escalation",
                        "requirements_answer",
                        f"V&V-Frage {len(answers)}/{len(questions)} beantwortet",
                        detail={"question": truncate(question, 200), "answer": truncate(answer, 200)},
                        to_agent="human_escalation",
                    )
                ],
            }

        # Alle Fragen beantwortet → Finalize + Confirm-Stage
        prompt = state.get("user_prompt") or ""
        final_vv = finalize_requirements_with_answers(
            prompt=prompt,
            draft=vv,
            answers=answers,
            phase=phase,
            state=state,
        )
        final_vv["open_questions"] = []
        return {
            "vv_qa_answers": answers,
            "vv_last_rejected_answer": None,
            "vv_requirements": final_vv,
            "human_approval_required": True,
            "escalation_reason": "requirements_confirm",
            "vv_needs_alignment": True,
            "current_agent": "human_escalation",
            "messages": [
                {
                    "role": "assistant",
                    "content": f"[Human Escalation] {len(answers)} Antworten erfasst – Anforderungsliste zur Bestätigung.",
                }
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "requirements_ready_confirm",
                    "V&V-Interview fertig → Bestätigung",
                    detail={"answers_count": len(answers)},
                    to_agent="human_escalation",
                )
            ],
        }

    # Keine offenen Fragen (z. B. Re-Entry) → Confirm
    prompt = state.get("user_prompt") or ""
    final_vv = finalize_requirements_with_answers(
        prompt=prompt,
        draft=vv,
        answers=answers,
        phase=phase,
        state=state,
    )
    final_vv["open_questions"] = []
    return {
        "vv_qa_answers": answers,
        "vv_requirements": final_vv,
        "human_approval_required": True,
        "escalation_reason": "requirements_confirm",
        "vv_needs_alignment": True,
        "current_agent": "human_escalation",
        "messages": [{"role": "assistant", "content": "[Human Escalation] Anforderungsliste zur Bestätigung."}],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "requirements_ready_confirm",
                "Keine offenen V&V-Fragen → Bestätigung",
                detail={"answers_count": len(answers)},
                to_agent="human_escalation",
            )
        ],
    }


def _handle_requirements_confirm(state: AgentState) -> AgentState:
    """Ein Confirm-Interrupt; bei Anpassung erneut Confirm (ohne Fragen-Reset)."""
    vv = dict(state.get("vv_requirements") or {}) if isinstance(state.get("vv_requirements"), dict) else {}
    phase = str(vv.get("phase") or state.get("vv_phase") or "concept")
    answers: list[dict[str, Any]] = list(state.get("vv_qa_answers") or [])

    payload = {
        "reason": "requirements_confirm",
        "vv_requirements": vv,
        "qa_answers": answers,
        "summary": vv.get("summary"),
        "phase": phase,
        "iteration_count": state.get("iteration_count", 0),
        "requirements_contract": state.get("requirements_contract"),
    }
    decision = interrupt(payload)
    logger.info("Human Escalation: Requirements-Bestätigung: %s", decision)

    if _decision_is_approve(decision):
        consulted = list(state.get("vv_consulted_phases") or [])
        if phase not in consulted:
            consulted.append(phase)
        vv_done = dict(vv)
        vv_done["open_questions"] = []

        from agents.design_spec import (
            build_design_spec_from_state,
            freeze_design_spec,
            validate_design_spec,
        )

        spec = validate_design_spec(
            freeze_design_spec(
                build_design_spec_from_state(
                    {
                        **state,
                        "vv_requirements": vv_done,
                        "vv_qa_answers": answers,
                    }
                )
            )
        )
        spec_note = ""
        if spec.validation_errors:
            spec_note = " Spec-Fehler: " + "; ".join(spec.validation_errors[:3])
        elif spec.validation_warnings:
            spec_note = " Spec-Hinweise: " + "; ".join(spec.validation_warnings[:2])

        return {
            "human_approval_required": False,
            "escalation_reason": None,
            "vv_needs_alignment": False,
            "vv_approved": True,
            "vv_requirements": vv_done,
            "vv_phase": vv_done.get("phase") or phase,
            "vv_qa_answers": answers,
            "vv_last_rejected_answer": None,
            "vv_consulted_phases": consulted,
            "design_spec": spec.to_state_dict(),
            "iteration_count": 0,
            "refinement_request": None,
            "current_agent": "human_escalation",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"[Human Escalation] Anforderungsliste bestätigt "
                        f"({len(vv_done.get('requirements') or [])} Punkte, "
                        f"{len(answers)} Antwort(en)). Design Spec eingefroren."
                        f"{spec_note}"
                    ),
                }
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "requirements_approved",
                    "Nutzer: Anforderungsliste freigegeben → Design Spec frozen",
                    detail={
                        "phase": phase,
                        "requirements_count": len(vv_done.get("requirements") or []),
                        "answers_count": len(answers),
                        "design_spec": {
                            "outer_mm": spec.outer_mm.model_dump() if spec.outer_mm else None,
                            "door_type": spec.door_type,
                            "compartments": spec.compartments,
                            "errors": spec.validation_errors,
                            "warnings": spec.validation_warnings,
                        },
                    },
                    to_agent="supervisor",
                )
            ],
        }

    feedback = _decision_feedback(decision) or "Bitte Anforderungen klarer und vollständiger formulieren."
    revised = revise_requirements_with_feedback(
        current=vv,
        feedback=feedback,
        answers=answers,
        state=state,
    )
    revised["open_questions"] = []
    return {
        "vv_requirements": revised,
        "human_approval_required": True,
        "escalation_reason": "requirements_confirm",
        "vv_needs_alignment": True,
        "current_agent": "human_escalation",
        "messages": [{"role": "assistant", "content": "[Human Escalation] Anforderungsliste angepasst – bitte erneut bestätigen."}],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "requirements_revised",
                "Nutzer: Anforderungsliste anpassen",
                detail={"feedback": truncate(feedback, 400)},
                to_agent="human_escalation",
            )
        ],
    }


def _handle_concept_clarification(state: AgentState) -> AgentState:
    """Eine Konzept-Klärungsfrage pro Aufruf; Antworten im State speichern."""
    critique = state.get("coherence_critique") if isinstance(state.get("coherence_critique"), dict) else {}
    questions = [str(q).strip() for q in (state.get("concept_open_questions") or []) if str(q).strip()]
    if not questions:
        from agents.coherence import unanswered_clarification_questions

        questions = unanswered_clarification_questions(critique, state.get("concept_qa_answers") or [])

    answers: list[dict[str, Any]] = list(state.get("concept_qa_answers") or [])
    pending = _pending_questions(questions, answers)

    logger.info(
        "Human Escalation: Konzept-Klärung (%s offen, %s beantwortet).",
        len(pending),
        len(answers),
    )

    if pending:
        question = pending[0]
        contract = state.get("requirements_contract") if isinstance(state.get("requirements_contract"), dict) else {}
        parts = contract.get("parts") if isinstance(contract.get("parts"), list) else []
        lean_contract = {
            "project_title": contract.get("project_title"),
            "parts": [
                {"name": (p.get("name") if isinstance(p, dict) else None) or f"Teil {i + 1}"}
                for i, p in enumerate(parts[:24])
            ],
        }
        gallery = state.get("concept_image_urls") or []
        if isinstance(gallery, list):
            gallery = gallery[:5]
        payload = {
            "reason": "concept_clarification",
            "question": question,
            "question_index": len(answers),
            "question_total": len(answers) + len(pending),
            "answered_so_far": answers,
            "phase": "concept_klarung",
            "draft_title": lean_contract.get("project_title"),
            "draft_summary": (critique or {}).get("summary"),
            "coherence_critique": {
                "summary": (critique or {}).get("summary"),
                "severity": (critique or {}).get("severity"),
                "verdict": (critique or {}).get("verdict"),
                "contradictions": ((critique or {}).get("contradictions") or [])[:6],
                "logic_gaps": ((critique or {}).get("logic_gaps") or [])[:6],
                "missing_information": ((critique or {}).get("missing_information") or [])[:6],
                "concept_issues": ((critique or {}).get("concept_issues") or [])[:6],
                "must_ask_user": ((critique or {}).get("must_ask_user") or [])[:6],
            },
            "concept_image_url": state.get("concept_image_url"),
            "concept_image_urls": gallery,
            "reference_asset_ids": (state.get("reference_asset_ids") or [])[:8],
            "requirements_contract": lean_contract,
            "iteration_count": state.get("iteration_count", 0),
        }
        logger.info(
            "Konzept-Klärung Interrupt Q%s/%s: %s",
            len(answers) + 1,
            len(answers) + len(pending),
            truncate(question, 120),
        )
        decision = interrupt(payload)
        answer = _interview_answer(decision)
        if not answer and isinstance(decision, dict) and decision.get("approved") is True:
            answer = str(decision.get("note") or "").strip() or "(keine Angabe)"
        if not answer:
            answer = "(keine Angabe)"
        answers = [*answers, {"question": question, "answer": answer}]
        still = _pending_questions(questions, answers)
        logger.info("Konzept-Klärung Q%d beantwortet (%s offen).", len(answers), len(still))

        if still:
            return {
                "concept_qa_answers": answers,
                "concept_open_questions": questions,  # volle Liste behalten (Index-Fortschritt)
                "concept_open_points_cleared": False,
                "concept_critiqued": True,
                "human_approval_required": True,
                "escalation_reason": "concept_clarification",
                "current_agent": "human_escalation",
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"[Human Escalation] Konzept-Klärung {len(answers)} erfasst, {len(still)} offen.",
                    }
                ],
                "agent_transcript": [
                    make_entry(
                        "human_escalation",
                        "concept_clarify_answer",
                        f"Konzept-Frage {len(answers)} beantwortet",
                        detail={"question": truncate(question, 200), "answer": truncate(answer, 200)},
                        to_agent="human_escalation",
                    )
                ],
            }

        # Alle geklärt → Concept Builder mit Feedback
        lines = [f"Q: {qa.get('question')}\nA: {qa.get('answer')}" for qa in answers]
        feedback = "Klärung offener Konzept-Punkte (verbindlich einarbeiten):\n" + "\n\n".join(lines)
        rounds = int(state.get("concept_clarify_rounds") or 0) + 1
        return {
            "human_approval_required": False,
            "escalation_reason": None,
            "concept_qa_answers": answers,
            "concept_open_questions": [],
            "concept_open_points_cleared": False,
            "concept_critiqued": False,
            "concept_clarify_rounds": rounds,
            "iteration_count": 0,
            "refinement_request": {
                "from_agent": "human",
                "target": "concept_builder",
                "reason": "concept_feedback",
                "feedback": feedback,
            },
            "current_agent": "human_escalation",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"[Human Escalation] {len(answers)} Konzept-Klärung(en) erfasst "
                        f"(Runde {rounds}) – Entwurf wird überarbeitet."
                    ),
                }
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "concept_clarified",
                    f"Nutzer: {len(answers)} offene Konzept-Punkt(e) beantwortet",
                    detail={"answers_count": len(answers), "clarify_round": rounds},
                    to_agent="concept_builder",
                )
            ],
        }

    # Nichts offen
    rounds = int(state.get("concept_clarify_rounds") or 0) + 1
    return {
        "human_approval_required": False,
        "escalation_reason": None,
        "concept_open_questions": [],
        "concept_open_points_cleared": True,
        "concept_critiqued": True,
        "concept_clarify_rounds": rounds,
        "current_agent": "human_escalation",
        "messages": [{"role": "system", "content": "[Human Escalation] Keine offenen Konzept-Fragen."}],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "concept_clarify_skip",
                "Keine offenen Konzept-Fragen",
                to_agent="supervisor",
            )
        ],
    }


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
    """Pausiert bei Konzept-Klärung, Confirm, Konzept- oder Requirements-Freigabe."""
    reason = state.get("escalation_reason")
    if reason == "concept_clarification":
        return _handle_concept_clarification(state)
    if reason == "concept_approval":
        return _handle_concept_approval(state)
    if reason == "requirements_approval":
        return _handle_requirements_approval(state)
    if reason == "requirements_confirm":
        return _handle_requirements_confirm(state)

    return _auto_continue(state)
