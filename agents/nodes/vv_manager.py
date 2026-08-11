"""V&V Manager – Requirements aus User-Prompt, phasenweise Vertiefung.

Ablauf bei Abstimmung:
1) Draft + Klärungsfragen erzeugen
2) Human Escalation fragt die Fragen nacheinander
3) Finale Anforderungsliste zur Bestätigung / Anpassung
"""

from __future__ import annotations

import logging
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_PHASES = ("concept", "design", "manufacturing")

_VV_SYSTEM = """Du bist der V&V-Manager einer CNC-/Holzwerkstatt (Verification & Validation).
Erstelle einen ersten Requirements-Entwurf und Klärungsfragen. Antworte NUR mit JSON:
{
  "title": string,
  "phase": "concept" | "design" | "manufacturing",
  "summary": string,
  "requirements": [
    {"id": "R1", "text": string, "priority": "must"|"should"|"could", "status": "proposed"|"agreed"|"open"}
  ],
  "open_questions": [string, ...],
  "needs_user_alignment": boolean,
  "acceptance_criteria": [string, ...]
}

Regeln:
- phase=concept: high-level Ziele, grobe Maße, Nutzungskontext – keine fertigen CNC-Pfade.
- phase=design: Geometrie, Materialien, Toleranzen, Schnittstellen.
- phase=manufacturing: prüfbare Fertigungs-/Abnahmekriterien, Maschinenbezug.
- open_questions: konkrete Klärungsfragen an den Nutzer (eine Sache pro Frage, Deutsch).
  Bei phase=concept: 2–5 Fragen zu Maßen, Material, Nutzung, Aufstellung/Einbau – außer alles ist klar.
- needs_user_alignment=true wenn Fragen oder Bestätigung der Liste nötig sind.
- Maximal 10 Draft-Requirements (werden nach den Antworten finalisiert).
"""

_FINALIZE_SYSTEM = """Du bist der V&V-Manager. Aus dem Draft und den Nutzerantworten erstellst du die
vollständige, finale Anforderungsliste. Antworte NUR mit JSON:
{
  "title": string,
  "phase": "concept" | "design" | "manufacturing",
  "summary": string,
  "requirements": [
    {"id": "R1", "text": string, "priority": "must"|"should"|"could", "status": "agreed"}
  ],
  "open_questions": [],
  "needs_user_alignment": false,
  "acceptance_criteria": [string, ...]
}
Regeln: Antworten des Nutzers verbindlich einarbeiten; Annahmen klar als solche kennzeichnen;
max. 12 Requirements; Deutsch; testbar formuliert.
"""

_REVISE_SYSTEM = """Du bist der V&V-Manager. Passe die Anforderungsliste anhand des Nutzer-Feedbacks an.
Antworte NUR mit dem gleichen JSON-Schema wie die finale Liste (open_questions leer, status=agreed).
"""


def _next_phase(current: str | None, *, concept_approved: bool, has_mfg_plan: bool) -> str:
    cur = current or "concept"
    if not concept_approved:
        return "concept"
    if has_mfg_plan and cur in ("concept", "design"):
        return "manufacturing"
    if concept_approved and cur == "concept":
        return "design"
    return cur if cur in _PHASES else "concept"


def _heuristic_requirements(prompt: str, phase: str, advisory: str | None) -> dict[str, Any]:
    title = (prompt.strip().split("\n")[0] or "Werkstück")[:80]
    reqs = [
        {
            "id": "R1",
            "text": f"Umsetzung der Nutzeranfrage: {title}",
            "priority": "must",
            "status": "proposed",
        },
        {
            "id": "R2",
            "text": "Bauteil(e) müssen mit vorhandener Werkstatt-Ausstattung fertigbar sein.",
            "priority": "must",
            "status": "proposed",
        },
        {
            "id": "R3",
            "text": "Maße und Materialien werden im Konzept konkretisiert und freigegeben.",
            "priority": "should",
            "status": "proposed",
        },
    ]
    questions = [
        "Welche Außenmaße (Breite × Höhe × Tiefe in mm oder cm) soll das Werkstück ungefähr haben?",
        "Welches Material bevorzugst du (z.B. Multiplex, Massivholz, MDF) und welche Plattenstärke?",
        "Wo wird das Stück aufgestellt bzw. eingebaut (Raum, Wand, freistehend)?",
    ]
    if phase == "design":
        reqs.append(
            {
                "id": "R4",
                "text": "Geometrie und Toleranzen sind für build123d/CNC spezifiziert.",
                "priority": "must",
                "status": "proposed",
            }
        )
        questions = [
            "Gibt es verbindliche Toleranzen oder Passungen?",
            "Welche Verbindungen/Beschläge sind vorgesehen?",
        ]
    if phase == "manufacturing":
        reqs.append(
            {
                "id": "R5",
                "text": "Schritt-für-Schritt-Arbeitsablauf und Maschinenwahl sind dokumentiert und prüfbar.",
                "priority": "must",
                "status": "proposed",
            }
        )
        questions = [
            "Welche Maschinen sollen priorisiert werden?",
            "Gibt es Fertigungs-Constraints (Zeit, Oberfläche, Kanten)?",
        ]
    return {
        "title": title,
        "phase": phase,
        "summary": f"Requirements-Entwurf ({phase}) aus Prompt abgeleitet."
        + (f" Advisory: {advisory[:200]}" if advisory else ""),
        "requirements": reqs,
        "open_questions": questions,
        "needs_user_alignment": True,
        "acceptance_criteria": [
            "Nutzer hat die Anforderungsliste bestätigt",
            "Sandbox-Export (STEP/STL) erfolgreich bei allen Teilen",
        ],
    }


def finalize_requirements_with_answers(
    *,
    prompt: str,
    draft: dict[str, Any],
    answers: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    """Baut die finale Anforderungsliste aus Draft + Q&A."""
    if is_llm_configured():
        try:
            user_block = (
                f"Phase: {phase}\nUser-Prompt:\n{prompt}\n\n"
                f"Draft-Requirements:\n{draft}\n\n"
                f"Nutzerantworten (Frage→Antwort):\n{answers}\n"
            )
            result = call_llm_json(_FINALIZE_SYSTEM, user_block, max_tokens=2200)
            if isinstance(result, dict) and result.get("requirements"):
                result["phase"] = phase if result.get("phase") not in _PHASES else result["phase"]
                result["open_questions"] = []
                result["needs_user_alignment"] = False
                for req in result.get("requirements") or []:
                    if isinstance(req, dict):
                        req.setdefault("status", "agreed")
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V Finalize LLM fehlgeschlagen: %s", exc)

    # Heuristik: Draft + Antworten als zusätzliche Requirements
    final = dict(draft)
    final["phase"] = phase
    final["open_questions"] = []
    final["needs_user_alignment"] = False
    reqs = list(final.get("requirements") or [])
    for i, qa in enumerate(answers, start=1):
        q = str(qa.get("question") or "").strip()
        a = str(qa.get("answer") or "").strip()
        if not a:
            continue
        reqs.append(
            {
                "id": f"A{i}",
                "text": f"{q} → {a}" if q else a,
                "priority": "must",
                "status": "agreed",
            }
        )
    for req in reqs:
        if isinstance(req, dict):
            req["status"] = "agreed"
    final["requirements"] = reqs
    final["summary"] = (
        str(final.get("summary") or "Anforderungsliste")
        + f" – finalisiert mit {len(answers)} Nutzerantwort(en)."
    )
    return final


def revise_requirements_with_feedback(
    *,
    current: dict[str, Any],
    feedback: str,
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    phase = str(current.get("phase") or "concept")
    if is_llm_configured():
        try:
            user_block = (
                f"Aktuelle Liste:\n{current}\n\n"
                f"Bisherige Q&A:\n{answers}\n\n"
                f"Nutzer-Feedback zur Anpassung:\n{feedback}\n"
            )
            result = call_llm_json(_REVISE_SYSTEM, user_block, max_tokens=2200)
            if isinstance(result, dict) and result.get("requirements"):
                result["phase"] = phase if result.get("phase") not in _PHASES else result["phase"]
                result["open_questions"] = []
                result["needs_user_alignment"] = False
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V Revise LLM fehlgeschlagen: %s", exc)

    updated = dict(current)
    reqs = list(updated.get("requirements") or [])
    reqs.append(
        {
            "id": f"F{len(reqs) + 1}",
            "text": f"Anpassung laut Nutzer: {feedback.strip()}",
            "priority": "must",
            "status": "agreed",
        }
    )
    updated["requirements"] = reqs
    updated["open_questions"] = []
    return updated


def vv_manager_node(state: AgentState) -> AgentState:
    prompt = state.get("user_prompt") or ""
    existing = state.get("vv_requirements") if isinstance(state.get("vv_requirements"), dict) else {}
    concept_approved = bool(state.get("concept_approved"))
    has_mfg = bool(state.get("manufacturing_plan"))
    target_phase = _next_phase(
        existing.get("phase") or state.get("vv_phase"),
        concept_approved=concept_approved,
        has_mfg_plan=has_mfg,
    )

    # Bereits gleiche Phase und freigegeben → nur weiterreichen
    if (
        existing
        and existing.get("phase") == target_phase
        and state.get("vv_approved")
        and not state.get("vv_needs_alignment")
    ):
        note = f"V&V-Requirements ({target_phase}) bereits abgestimmt – keine Änderung."
        consulted = list(state.get("vv_consulted_phases") or [])
        if target_phase not in consulted:
            consulted.append(target_phase)
        return {
            "refinement_request": None,
            "vv_consulted_phases": consulted,
            "current_agent": "vv_manager",
            "messages": [{"role": "assistant", "content": f"[V&V Manager] {note}"}],
            "agent_transcript": [
                make_entry(
                    "vv_manager",
                    "skip_unchanged",
                    note,
                    detail={"phase": target_phase},
                    to_agent="supervisor",
                )
            ],
        }

    advisory = state.get("advisory_notes") or ""
    flex = state.get("flexible_specialist_profile") or {}
    mfg = state.get("manufacturing_plan") or {}
    contract = state.get("requirements_contract") or {}
    guidance = get_agent_guidance("vv_manager")

    refinement = state.get("refinement_request") or {}
    feedback = ""
    if refinement.get("reason") == "requirements_feedback":
        feedback = str(refinement.get("feedback") or "").strip()

    user_block = (
        f"Zielphase: {target_phase}\n"
        f"User-Prompt:\n{prompt}\n\n"
        f"Flexible-Specialist-Profil: {flex}\n"
        f"Advisory-Notes: {advisory}\n"
        f"Bisherige V&V: {existing}\n"
        f"Concept-Contract: { {k: contract.get(k) for k in ('project_title', 'parts') if contract} }\n"
        f"Fertigungskonzept (Kurz): { {k: mfg.get(k) for k in ('feasible', 'machines_needed', 'summary') if mfg} }\n"
        f"Zusatz-Guidance: {guidance or '(keine)'}\n"
    )
    if feedback:
        user_block += f"\nNutzer-Feedback zu Requirements:\n{feedback}\nBitte Draft und Fragen entsprechend anpassen.\n"

    result: dict[str, Any]
    if is_llm_configured():
        try:
            result = call_llm_json(_VV_SYSTEM, user_block, max_tokens=2200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V LLM fehlgeschlagen: %s", exc)
            result = _heuristic_requirements(prompt, target_phase, advisory if isinstance(advisory, str) else None)
    else:
        result = _heuristic_requirements(prompt, target_phase, advisory if isinstance(advisory, str) else None)

    result["phase"] = target_phase if result.get("phase") not in _PHASES else result["phase"]
    questions = [str(q).strip() for q in (result.get("open_questions") or []) if str(q).strip()]
    result["open_questions"] = questions

    # Konzeptphase: immer Interview/Bestätigung; später nur bei echten Fragen
    if target_phase == "concept":
        needs = True
        if not questions:
            questions = _heuristic_requirements(prompt, target_phase, None)["open_questions"]
            result["open_questions"] = questions
        result["needs_user_alignment"] = True
    else:
        needs = bool(result.get("needs_user_alignment")) and bool(questions)
        if not questions:
            needs = False

    note = (
        f"Requirements-Entwurf ({result.get('phase')}): {len(result.get('requirements') or [])} Punkte"
        + (f", {len(questions)} Klärungsfrage(n) → Nutzer-Interview" if needs else ", freigegeben/weiter")
        + "."
    )

    updates: AgentState = {
        "vv_requirements": result,
        "vv_phase": result.get("phase"),
        "vv_needs_alignment": needs,
        "vv_qa_answers": [],
        "refinement_request": None,
        "current_agent": "vv_manager",
        "messages": [{"role": "assistant", "content": f"[V&V Manager] {note}"}],
        "agent_transcript": [
            make_entry(
                "vv_manager",
                "requirements_draft",
                note,
                detail={
                    "phase": result.get("phase"),
                    "requirements_count": len(result.get("requirements") or []),
                    "open_questions": questions,
                    "needs_user_alignment": needs,
                    "title": result.get("title"),
                    "summary": truncate(str(result.get("summary") or ""), 400),
                },
                to_agent="human_escalation" if needs else "supervisor",
            )
        ],
    }

    if needs:
        updates["human_approval_required"] = True
        updates["escalation_reason"] = "requirements_approval"
        updates["vv_approved"] = False
    else:
        updates["human_approval_required"] = False
        if state.get("escalation_reason") == "requirements_approval":
            updates["escalation_reason"] = None
        updates["vv_approved"] = True
        consulted = list(state.get("vv_consulted_phases") or [])
        phase = str(result.get("phase") or target_phase)
        if phase not in consulted:
            consulted.append(phase)
        updates["vv_consulted_phases"] = consulted

    return updates
