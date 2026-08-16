"""V&V Manager – Requirements aus User-Prompt, phasenweise Vertiefung.

Ablauf bei Abstimmung:
1) Draft + Klärungsfragen erzeugen
2) Human Escalation fragt die Fragen nacheinander
3) Finale Anforderungsliste zur Bestätigung / Anpassung
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
  Bei phase=concept: 0–5 Fragen – nur zu Dingen, die NICHT schon klar sind.
  Bei phase=design|manufacturing: KEINE Fragen wiederholen, die in concept schon geklärt
  wurden (Couch, Regalwand, Maße, Platzierung, Bestand). open_questions leer lassen,
  außer bei wirklich neuen Fertigungs-/Toleranzlücken.
- needs_user_alignment=true nur in phase=concept wenn Fragen nötig sind.
  In design/manufacturing: needs_user_alignment=false (Liste aus prioren Antworten ableiten).
- Maximal 10 Draft-Requirements (werden nach den Antworten finalisiert).

WICHTIG bei angehängten Referenzfotos / Grundriss mit Bemaßung:
- PRIORITÄT: Fotos/Grundriss sind Ground Truth – höher als reine Textannahmen.
- Wenn ein Grundriss angehängt ist: lege mind. ein must-Requirement an, das diesen Anhang
  explizit verankert (z.B. „Grundriss-Anhang … ist verbindlich für Wände/Maße/Platzierung“).
- Lies sichtbare Maße, Raumform und vorhandene Objekte und schreibe sie als Requirements
  (status=proposed), z.B. „Raummaß laut Grundriss: Breite … × Länge …“,
  „Vorhandener IKEA-Schrank laut Foto bleibt stehen“.
- Stelle KEINE Fragen nach Raummaßen, Raumgröße, Breite/Länge/Höhe des Zimmers,
  Grundriss-Maßen oder Aufstellort, wenn diese Informationen im Grundriss/in den Fotos
  erkennbar sind bzw. der Nutzer einen bemessenen Grundriss angehängt hat.
- Frage nur nach echten Lücken (Material, Belastung, Optik, CNC-Details), nicht nach dem,
  was schon im Bild steht.
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
Wenn Referenz-/Grundrissmaße schon im Draft stehen, beibehalten und nicht als „offen“ behandeln.
Requirements mit id REF-PLAN-*, REF-PHOTOS, REF-VISION unverändert übernehmen (höchste Priorität).
"""

_REVISE_SYSTEM = """Du bist der V&V-Manager. Passe die Anforderungsliste anhand des Nutzer-Feedbacks an.
Antworte NUR mit dem gleichen JSON-Schema wie die finale Liste (open_questions leer, status=agreed).
"""


def _is_redundant_room_dimension_question(question: str) -> bool:
    """True, wenn die Frage Raum-/Grundrissmaße abfragt (oft schon im Anhang)."""
    q = (question or "").lower()
    roomish = any(
        k in q
        for k in (
            "raum",
            "zimmer",
            "grundriss",
            "fläche",
            "flaeche",
            "quadratmeter",
            "wohnraum",
            "aufstell",
            "einbauort",
        )
    )
    sizeish = any(
        k in q
        for k in (
            "maß",
            "mass",
            "abmess",
            "größe",
            "groesse",
            "bemaß",
            "bemass",
            "breite",
            "länge",
            "laenge",
            "tiefe",
            "höhe",
            "hoehe",
            "mm",
            "cm",
        )
    )
    if roomish and sizeish:
        return True
    if re.search(r"raumm[aä]ß|zimmergröße|raumgröße|grundriss.*maß", q):
        return True
    return False


def _next_phase(current: str | None, *, concept_approved: bool, has_mfg_plan: bool) -> str:
    cur = current or "concept"
    if not concept_approved:
        return "concept"
    if has_mfg_plan and cur in ("concept", "design"):
        return "manufacturing"
    if concept_approved and cur == "concept":
        return "design"
    return cur if cur in _PHASES else "concept"


def _prior_qa_pairs(state: AgentState) -> list[dict[str, Any]]:
    """Alle bisherigen Nutzerantworten (V&V + Konzept-Klärung)."""
    out: list[dict[str, Any]] = []
    for key in ("vv_qa_answers", "concept_qa_answers"):
        for item in state.get(key) or []:
            if isinstance(item, dict) and (
                str(item.get("question") or "").strip() or str(item.get("answer") or "").strip()
            ):
                out.append(item)
    return out


def _question_covered_by_prior(question: str, prior: list[dict[str, Any]]) -> bool:
    """True wenn die Frage inhaltlich schon beantwortet wurde (kein zweites Interview)."""
    key = re.sub(r"\s+", " ", (question or "").strip()).lower()
    if not key:
        return True
    toks = {t for t in re.findall(r"[a-z0-9äöüß]{4,}", key)}
    for item in prior:
        prev_q = re.sub(r"\s+", " ", str(item.get("question") or "").strip()).lower()
        if not prev_q:
            continue
        if prev_q == key:
            return True
        prev_toks = {t for t in re.findall(r"[a-z0-9äöüß]{4,}", prev_q)}
        if toks and prev_toks and len(toks & prev_toks) / max(len(toks), 1) >= 0.5:
            return True
    return False


def _filter_new_questions(questions: list[str], prior: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for q in questions:
        text = str(q).strip()
        if not text:
            continue
        low = text.lower()
        if low in seen or _question_covered_by_prior(text, prior):
            continue
        seen.add(low)
        out.append(text)
    return out


def _heuristic_requirements(
    prompt: str,
    phase: str,
    advisory: str | None,
    *,
    has_reference_images: bool = False,
) -> dict[str, Any]:
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
            "text": (
                "Raummaße und Aufstellung laut angehängtem Grundriss/Referenzfoto "
                "(sichtbare Bemaßung gilt als verbindlich)."
                if has_reference_images
                else "Maße und Materialien werden im Konzept konkretisiert und freigegeben."
            ),
            "priority": "should",
            "status": "proposed",
        },
    ]
    if has_reference_images:
        questions = [
            "Welches Material bevorzugst du (z.B. Multiplex, Massivholz, MDF) und welche Plattenstärke?",
            "Gibt es besondere Anforderungen an Belastung, Stauraum oder Optik, die im Grundriss nicht stehen?",
        ]
    else:
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
        "summary": (
            f"Requirements-Entwurf ({phase})"
            + (" – Grundriss/Referenzfotos als Maßgrundlage." if has_reference_images else ".")
            + (f" Advisory: {advisory[:200]}" if advisory else "")
        ),
        "requirements": reqs,
        "open_questions": questions,
        "needs_user_alignment": True,
        "acceptance_criteria": [
            "Nutzer hat die Anforderungsliste bestätigt",
            "Sandbox-Export (STEP/STL) erfolgreich bei allen Teilen",
        ],
    }


def _apply_reference_anchors(vv: dict[str, Any], state: AgentState | None) -> dict[str, Any]:
    """Verankert Grundriss-/Foto-Anhänge als must-Requirements vorne in der Liste."""
    if not state:
        return vv
    from agents.reference_images import (
        build_reference_requirement_anchors,
        merge_reference_requirements,
    )

    anchors = build_reference_requirement_anchors(state)
    if not anchors:
        return vv
    out = dict(vv)
    out["requirements"] = merge_reference_requirements(out.get("requirements"), anchors)
    logger.info("V&V: %s Referenz-Requirement(s) verankert", len(anchors))
    return out


def finalize_requirements_with_answers(
    *,
    prompt: str,
    draft: dict[str, Any],
    answers: list[dict[str, Any]],
    phase: str,
    state: AgentState | None = None,
) -> dict[str, Any]:
    """Baut die finale Anforderungsliste aus Draft + Q&A."""
    from agents.reference_images import reference_ground_truth_block

    gt = reference_ground_truth_block(state) if state else ""
    if is_llm_configured():
        try:
            user_block = (
                f"Phase: {phase}\nUser-Prompt:\n{prompt}\n\n"
                f"{gt}\n\n"
                f"Draft-Requirements:\n{draft}\n\n"
                f"Nutzerantworten (Frage→Antwort):\n{answers}\n"
                "REF-*-Requirements und Ground-Truth-Fotos haben Vorrang vor widersprüchlichen Antworten.\n"
            )
            result = call_llm_json(_FINALIZE_SYSTEM, user_block, max_tokens=2200)
            if isinstance(result, dict) and result.get("requirements"):
                result["phase"] = phase if result.get("phase") not in _PHASES else result["phase"]
                result["open_questions"] = []
                result["needs_user_alignment"] = False
                for req in result.get("requirements") or []:
                    if isinstance(req, dict):
                        req.setdefault("status", "agreed")
                return _apply_reference_anchors(result, state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V Finalize LLM fehlgeschlagen: %s", exc)

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
    return _apply_reference_anchors(final, state)


def revise_requirements_with_feedback(
    *,
    current: dict[str, Any],
    feedback: str,
    answers: list[dict[str, Any]],
    state: AgentState | None = None,
) -> dict[str, Any]:
    phase = str(current.get("phase") or "concept")
    if is_llm_configured():
        try:
            user_block = (
                f"Aktuelle Liste:\n{current}\n\n"
                f"Bisherige Q&A:\n{answers}\n\n"
                f"Nutzer-Feedback zur Anpassung:\n{feedback}\n"
                "REF-PLAN-*/REF-PHOTOS/REF-VISION und Foto-Ground-Truth beibehalten.\n"
            )
            result = call_llm_json(_REVISE_SYSTEM, user_block, max_tokens=2200)
            if isinstance(result, dict) and result.get("requirements"):
                result["phase"] = phase if result.get("phase") not in _PHASES else result["phase"]
                result["open_questions"] = []
                result["needs_user_alignment"] = False
                return _apply_reference_anchors(result, state)
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
    return _apply_reference_anchors(updated, state)


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

    # Interview/Confirm läuft bereits → keinen neuen Draft (sonst Fragen doppelt + Answers weg)
    reason = str(state.get("escalation_reason") or "")
    prior_answers = list(state.get("vv_qa_answers") or [])
    prior_questions = [
        str(q).strip() for q in (existing.get("open_questions") or []) if str(q).strip()
    ]
    interview_active = bool(state.get("vv_needs_alignment")) and (
        reason
        in {
            "requirements_approval",
            "requirements_confirm",
            "requirements_question",
        }
        or bool(prior_answers)
        or bool(prior_questions)
    )
    if interview_active and not (
        isinstance(state.get("refinement_request"), dict)
        and (state.get("refinement_request") or {}).get("reason") == "requirements_feedback"
    ):
        note = (
            f"V&V-Interview ({target_phase}) läuft bereits "
            f"({len(prior_answers)} Antwort(en), {len(prior_questions)} Frage(n)) – kein Neu-Draft."
        )
        return {
            "vv_requirements": existing or state.get("vv_requirements"),
            "vv_phase": (existing or {}).get("phase") or state.get("vv_phase") or target_phase,
            "vv_needs_alignment": True,
            "vv_qa_answers": prior_answers,
            "human_approval_required": True,
            "escalation_reason": reason
            if reason in {"requirements_approval", "requirements_confirm", "requirements_question"}
            else ("requirements_confirm" if prior_answers and not prior_questions else "requirements_approval"),
            "refinement_request": None,
            "current_agent": "vv_manager",
            "messages": [{"role": "assistant", "content": f"[V&V Manager] {note}"}],
            "agent_transcript": [
                make_entry(
                    "vv_manager",
                    "skip_interview_in_progress",
                    note,
                    detail={
                        "phase": target_phase,
                        "answers_count": len(prior_answers),
                        "questions_count": len(prior_questions),
                        "escalation_reason": reason,
                    },
                    to_agent="human_escalation",
                )
            ],
        }

    advisory = state.get("advisory_notes") or ""
    flex = state.get("flexible_specialist_profile") or {}
    mfg = state.get("manufacturing_plan") or {}
    contract = state.get("requirements_contract") or {}
    guidance = get_agent_guidance("vv_manager")

    from agents.reference_images import (
        ensure_reference_vision_brief,
        load_reference_images_for_llm,
        reference_context_block,
        resolve_reference_asset_ids,
    )

    brief, brief_updates = ensure_reference_vision_brief(state)
    state_for_refs = {**state, **brief_updates} if brief_updates else state
    ref_ids = resolve_reference_asset_ids(state_for_refs)
    ref_images = load_reference_images_for_llm(state_for_refs, limit=4)
    has_refs = bool(ref_images) or bool(ref_ids)

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
        f"{reference_context_block(state_for_refs)}\n"
    )
    if ref_images:
        user_block += (
            f"\n{len(ref_images)} Referenzbild(er)/Grundriss sind diesem Call als Bilder beigefügt. "
            "Lies sichtbare Bemaßungen und Raumkontext daraus. "
            "Frage NICHT nach Raummaßen, die im Bild stehen.\n"
        )
    elif ref_ids:
        user_block += (
            "\nEs sind Referenz-Asset-IDs angehängt (vermutlich Grundriss/Fotos). "
            "Behandle angegebene Maße im Prompt bzw. im Vision-Brief als gegeben; "
            "frage nicht erneut nach Raummaßen.\n"
        )
    if feedback:
        user_block += f"\nNutzer-Feedback zu Requirements:\n{feedback}\nBitte Draft und Fragen entsprechend anpassen.\n"

    # Schonungslose Intake-Kohärenz (Prompt ↔ Fotos) vor Fragen
    coherence: dict[str, Any] | None = None
    try:
        from agents.coherence import analyze_intake_coherence, format_coherence_block

        coherence = analyze_intake_coherence(state_for_refs)
        cblock = format_coherence_block(coherence)
        if cblock:
            user_block += (
                f"\n{cblock}\n"
                "Nutze Widersprüche/Lücken/fehlende Infos: formuliere must-Requirements und "
                "open_questions schonungslos. Vertusche nichts, was Text und Bilder nicht klären.\n"
            )
            for q in (coherence.get("must_ask_user") or [])[:5]:
                qs = str(q).strip()
                if qs:
                    user_block += f"\nPflichtfrage-Kandidat: {qs}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Intake-Kohärenz übersprungen: %s", exc)
        coherence = None

    advisory_str = advisory if isinstance(advisory, str) else None
    result: dict[str, Any]
    if is_llm_configured():
        try:
            result = call_llm_json(
                _VV_SYSTEM,
                user_block,
                max_tokens=2200,
                images=ref_images or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V LLM fehlgeschlagen: %s", exc)
            result = _heuristic_requirements(
                prompt, target_phase, advisory_str, has_reference_images=has_refs
            )
    else:
        result = _heuristic_requirements(
            prompt, target_phase, advisory_str, has_reference_images=has_refs
        )

    result["phase"] = target_phase if result.get("phase") not in _PHASES else result["phase"]
    questions = [str(q).strip() for q in (result.get("open_questions") or []) if str(q).strip()]
    prior_qa = _prior_qa_pairs(state)
    consulted_phases = list(state.get("vv_consulted_phases") or [])

    # Redundante Raummaß-Fragen entfernen, wenn Referenzen/Grundriss da sind
    if has_refs:
        filtered = [q for q in questions if not _is_redundant_room_dimension_question(q)]
        dropped = len(questions) - len(filtered)
        if dropped:
            logger.info("V&V: %s Raummaß-Frage(n) wegen Referenzfotos/Grundriss verworfen", dropped)
        questions = filtered

    before = len(questions)
    questions = _filter_new_questions(questions, prior_qa)
    if before - len(questions):
        logger.info(
            "V&V: %s Frage(n) wegen früherer Antworten übersprungen (phase=%s)",
            before - len(questions),
            target_phase,
        )

    # Konzeptphase: genau EIN Nutzer-Interview + Confirm
    if target_phase == "concept":
        needs = True
        if not questions:
            questions = _filter_new_questions(
                _heuristic_requirements(
                    prompt, target_phase, None, has_reference_images=has_refs
                )["open_questions"],
                prior_qa,
            )
        result["needs_user_alignment"] = True
    else:
        # design/manufacturing: kein zweites Freigabe-Ritual (Logs: gleiche Fragen 3×)
        if "concept" in consulted_phases or prior_qa:
            questions = []
            needs = False
            result["needs_user_alignment"] = False
            logger.info(
                "V&V: phase=%s ohne Nutzer-Interview (concept freigegeben / %s Q&A)",
                target_phase,
                len(prior_qa),
            )
        else:
            needs = bool(result.get("needs_user_alignment")) and bool(questions)
            if not questions:
                needs = False

    result["open_questions"] = questions
    state_for_anchors = {**state_for_refs, **brief_updates} if brief_updates else state_for_refs
    if coherence:
        state_for_anchors = {**state_for_anchors, "coherence_critique": coherence}
    result = _apply_reference_anchors(result, state_for_anchors)

    if coherence and target_phase == "concept":
        extra = [str(q).strip() for q in (coherence.get("must_ask_user") or []) if str(q).strip()]
        for q in extra:
            if (
                q not in questions
                and not (has_refs and _is_redundant_room_dimension_question(q))
                and not _question_covered_by_prior(q, prior_qa)
            ):
                questions.append(q)
        questions = questions[:6]
        result["open_questions"] = questions
        if coherence.get("severity") in ("gaps", "blocking") and questions:
            result["needs_user_alignment"] = True
            needs = True

    if target_phase != "concept" and prior_qa and not needs:
        try:
            result = finalize_requirements_with_answers(
                prompt=prompt,
                draft=result,
                answers=prior_qa,
                phase=target_phase,
                state=state_for_anchors,
            )
            result["open_questions"] = []
            result["needs_user_alignment"] = False
        except Exception as exc:  # noqa: BLE001
            logger.warning("V&V Auto-Finalize (%s) fehlgeschlagen: %s", target_phase, exc)

    note = (
        f"Requirements-Entwurf ({result.get('phase')}): {len(result.get('requirements') or [])} Punkte"
        + (
            f", {len(questions)} Klärungsfrage(n) → Nutzer-Interview"
            if needs
            else ", auto-weiter (kein erneutes Interview)"
        )
        + (f" ({len(ref_images)} Referenzbild(er) gesehen)" if ref_images else "")
        + (f", Kohärenz={coherence.get('severity')}" if coherence else "")
        + "."
    )

    updates: AgentState = {
        "vv_requirements": result,
        "vv_phase": result.get("phase"),
        "vv_needs_alignment": needs,
        "vv_qa_answers": [] if needs else prior_qa,
        "human_approval_required": bool(needs),
        "escalation_reason": "requirements_approval" if needs else None,
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
                    "auto_skip_interview": (not needs) and target_phase != "concept",
                    "prior_qa_count": len(prior_qa),
                    "title": result.get("title"),
                    "summary": truncate(str(result.get("summary") or ""), 400),
                    "reference_asset_ids": ref_ids,
                    "reference_images_sent": len(ref_images),
                    "reference_vision_brief": truncate(brief, 500) if brief else None,
                    "coherence_severity": (coherence or {}).get("severity"),
                },
                to_agent="human_escalation" if needs else "supervisor",
            )
        ],
    }
    updates.update(brief_updates)
    if coherence:
        updates["coherence_critique"] = coherence
    if ref_ids and not state.get("reference_asset_ids"):
        updates["reference_asset_ids"] = ref_ids

    if needs:
        updates["vv_approved"] = False
    else:
        updates["vv_approved"] = True
        updates["vv_needs_alignment"] = False
        consulted = list(state.get("vv_consulted_phases") or [])
        phase = str(result.get("phase") or target_phase)
        if phase not in consulted:
            consulted.append(phase)
        updates["vv_consulted_phases"] = consulted

    return updates
