"""Konzept-Kritiker – prüft den ausgearbeiteten Entwurf gegen Fotos/Prompt/VV.

Offene Punkte werden vor der Konzept-Freigabe als Klärungsfragen an den Nutzer
gestellt; erst wenn sie beantwortet und eingearbeitet sind, folgt die Freigabe.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.coherence import (
    analyze_concept_coherence,
    format_coherence_block,
    merge_coherence_reports,
    unanswered_clarification_questions,
)
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_MAX_CLARIFY_ROUNDS = 2


def concept_critic_node(state: AgentState) -> AgentState:
    guidance = get_agent_guidance("concept_critic")
    answers = list(state.get("concept_qa_answers") or [])
    # V&V-Antworten mitzählen, sonst kommen Couch/Regal-Fragen ein zweites Mal
    for item in state.get("vv_qa_answers") or []:
        if isinstance(item, dict):
            answers.append(item)
    existing_qs = [str(q).strip() for q in (state.get("concept_open_questions") or []) if str(q).strip()]

    # Interview läuft bereits → nicht neu generieren (sonst Fragen doppelt)
    if (
        state.get("escalation_reason") == "concept_clarification"
        and state.get("human_approval_required")
        and existing_qs
        and len(answers) < len(existing_qs)
    ):
        note = (
            f"Konzept-Klärung läuft bereits ({len(answers)}/{len(existing_qs)}) – kein neuer Kritik-Draft."
        )
        logger.info(note)
        return {
            "concept_critiqued": True,
            "concept_open_points_cleared": False,
            "concept_open_questions": existing_qs,
            "concept_qa_answers": answers,
            "human_approval_required": True,
            "escalation_reason": "concept_clarification",
            "refinement_request": None,
            "current_agent": "concept_critic",
            "messages": [{"role": "assistant", "content": f"[Konzept-Kritiker] {note}"}],
            "agent_transcript": [
                make_entry(
                    "concept_critic",
                    "skip_clarify_in_progress",
                    note,
                    detail={"answers": len(answers), "questions": len(existing_qs)},
                    to_agent="human_escalation",
                )
            ],
        }

    prior = state.get("coherence_critique") if isinstance(state.get("coherence_critique"), dict) else {}
    review = analyze_concept_coherence(state)
    merged = merge_coherence_reports(prior if prior.get("phase") == "intake" else None, review)
    merged["phase"] = "concept"
    if guidance:
        merged["meta_guidance_note"] = str(guidance)[:400]

    # Deterministische Spec-Checks (kein LLM) – harte Fehler als Klärungsfragen
    from agents.design_spec import (
        build_design_spec_from_state,
        load_design_spec,
        validate_design_spec,
    )

    spec = load_design_spec(state.get("design_spec"))
    if spec is None:
        spec = build_design_spec_from_state(state)
    spec = validate_design_spec(spec)
    hard_qs: list[str] = []
    for err in spec.validation_errors:
        hard_qs.append(f"Design-Spec-Fehler beheben: {err}")
        merged.setdefault("concept_issues", []).append(err)
        merged.setdefault("logic_gaps", []).append(err)
    for warn in spec.validation_warnings[:3]:
        merged.setdefault("assumptions_made_by_user_or_system", []).append(warn)
    if hard_qs:
        merged["severity"] = "blocking"
        merged["verdict"] = "needs_user_input"
        must = list(merged.get("must_ask_user") or [])
        for q in hard_qs:
            if q not in must:
                must.append(q)
        merged["must_ask_user"] = must[:8]

    open_qs = unanswered_clarification_questions(merged, answers)
    for q in hard_qs:
        if q not in open_qs:
            open_qs.insert(0, q)
    open_qs = open_qs[:8]
    rounds = int(state.get("concept_clarify_rounds") or 0)
    # Harte Spec-Fehler nicht per force_approval wegdrücken
    force_approval = rounds >= _MAX_CLARIFY_ROUNDS and bool(open_qs) and not hard_qs

    if force_approval:
        logger.warning(
            "Konzept-Kritik: %s offene Frage(n) nach %s Runden – Freigabe mit Restrisiken",
            len(open_qs),
            rounds,
        )
        open_qs = []
        merged["summary"] = (
            (str(merged.get("summary") or "") + " ").strip()
            + f"Nach {rounds} Klärungsrunde(n) verbleibende Punkte als Restrisiko an Nutzer übergeben."
        ).strip()

    block = format_coherence_block(merged)
    severity = str(merged.get("severity") or "ok")
    verdict = str(merged.get("verdict") or "ready_for_user")
    n_issues = (
        len(merged.get("contradictions") or [])
        + len(merged.get("logic_gaps") or [])
        + len(merged.get("missing_information") or [])
        + len(merged.get("concept_issues") or [])
    )

    advisory = str(state.get("advisory_notes") or "")
    critique_note = f"[Konzept-Kritiker]\n{block or merged.get('summary') or ''}"
    merged_advisory = f"{advisory}\n\n{critique_note}".strip() if advisory else critique_note

    base: dict[str, Any] = {
        "coherence_critique": merged,
        "concept_critiqued": True,
        "concept_open_questions": open_qs,
        "advisory_notes": merged_advisory[:6000],
        "concept_approved": False,
        "refinement_request": None,
        "current_agent": "concept_critic",
        "design_spec": spec.to_state_dict(),
    }

    if open_qs:
        note = (
            f"Konzept-Kritik: {len(open_qs)} offene Punkt(e) müssen vor Freigabe geklärt werden "
            f"(severity={severity}, verdict={verdict}, {n_issues} Befund(e))."
        )
        if block:
            note = f"{note}\n{block[:900]}"
        logger.info("Konzept-Kritiker → Klärungsinterview (%s Fragen)", len(open_qs))
        return {
            **base,
            "concept_open_points_cleared": False,
            "human_approval_required": True,
            "escalation_reason": "concept_clarification",
            "messages": [{"role": "assistant", "content": f"[Konzept-Kritiker] {note[:1500]}"}],
            "agent_transcript": [
                make_entry(
                    "concept_critic",
                    "needs_clarification",
                    note[:1500],
                    detail={
                        "severity": severity,
                        "verdict": verdict,
                        "open_questions": open_qs,
                        "clarify_round": rounds,
                        "contradictions": (merged.get("contradictions") or [])[:8],
                        "logic_gaps": (merged.get("logic_gaps") or [])[:8],
                        "missing_information": (merged.get("missing_information") or [])[:8],
                        "concept_issues": (merged.get("concept_issues") or [])[:8],
                        "summary": truncate(str(merged.get("summary") or ""), 500),
                    },
                    to_agent="human_escalation",
                )
            ],
        }

    note = (
        f"Konzept-Kritik OK für Freigabe (severity={severity}, verdict={verdict}, "
        f"{n_issues} dokumentierte Punkt(e), keine offenen Klärungsfragen)."
    )
    if force_approval:
        note = (
            f"Konzept-Kritik: Freigabe trotz Restrisiken nach {rounds} Klärungsrunde(n) "
            f"(severity={severity}, {n_issues} Punkt(e))."
        )
    if block:
        note = f"{note}\n{block[:900]}"
    logger.info("Konzept-Kritiker → Freigabe (offene Punkte geklärt)")
    return {
        **base,
        "concept_open_points_cleared": True,
        "concept_open_questions": [],
        "human_approval_required": True,
        "escalation_reason": "concept_approval",
        "messages": [{"role": "assistant", "content": f"[Konzept-Kritiker] {note[:1500]}"}],
        "agent_transcript": [
            make_entry(
                "concept_critic",
                "concept_review",
                note[:1500],
                detail={
                    "severity": severity,
                    "verdict": verdict,
                    "open_points_cleared": True,
                    "force_approval": force_approval,
                    "contradictions": (merged.get("contradictions") or [])[:8],
                    "logic_gaps": (merged.get("logic_gaps") or [])[:8],
                    "missing_information": (merged.get("missing_information") or [])[:8],
                    "concept_issues": (merged.get("concept_issues") or [])[:8],
                    "summary": truncate(str(merged.get("summary") or ""), 500),
                },
                to_agent="human_escalation",
            )
        ],
    }
