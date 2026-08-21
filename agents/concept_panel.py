"""Konzept-Jury: Schulnoten vor User-Freigabe.

Regeln:
- Jury bewertet nacheinander (1–6, 1 = best).
- Nach jeder Jury-Runde wird das Konzept dem User vorgestellt (auch bei Note ≥5).
- Kein automatischer Rebuild – der User entscheidet: freigeben oder nächste Runde.
- Der User kann mitbewerten und Feedback für den Concept Builder geben.
- Snapshots in concept_panel_history für Nachvollziehbarkeit.
"""

from __future__ import annotations

import copy
from typing import Any

from app.services.agent_workflow_store import is_agent_enabled

JURY_ORDER = (
    "flexible_specialist",
    "interior_architect",
    "vv_manager",
    "concept_critic",
    "fertigung_specialist",
)

MAX_PANEL_ROUNDS = 4
MANGELHAFT_MIN = 5

_CONCEPT_SNAPSHOT_KEYS = (
    "requirements_contract",
    "concept_sketch_svg",
    "concept_image_url",
    "concept_image_urls",
    "design_spec",
    "coherence_critique",
)

_GRADE_LABELS = {
    1: "sehr gut",
    2: "gut",
    3: "befriedigend",
    4: "ausreichend",
    5: "mangelhaft",
    6: "ungenügend",
}

_ROLE_FOCUS = {
    "flexible_specialist": (
        "Werkstatt-Machbarkeit, Material/Maschinen-Realismus, Nutzerwunsch vs. CNC-Grenzen."
    ),
    "interior_architect": (
        "Raumbezug, Platzierung, Proportionen zu Fotos/Grundriss, links/rechts der Ecke."
    ),
    "vv_manager": (
        "Requirements-Treue, Maße/Design Spec, Widersprüche Prompt↔Konzept, Abnahmekriterien."
    ),
    "concept_critic": (
        "Bild↔Text↔Konzept-Kohärenz, Lücken, geometrische/logische Mängel, Spec-Fehler."
    ),
    "fertigung_specialist": (
        "Fertigungstauglichkeit, Plattenzuschnitt, Schiebetür-Hardware, Spannweiten/Mittelwand."
    ),
}


def grade_label(grade: int | float | None) -> str:
    try:
        g = int(round(float(grade or 0)))
    except (TypeError, ValueError):
        return "unbekannt"
    return _GRADE_LABELS.get(g, f"Note {g}")


def role_focus(agent_id: str) -> str:
    return _ROLE_FOCUS.get(agent_id, "Gesamtqualität des Konzepts.")


def jury_agents(state: dict[str, Any] | None = None) -> list[str]:
    """Jury = Supervisor-Roster ∩ enabled ∩ JURY_ORDER (Komplexität steuert die Menge)."""
    enabled = [aid for aid in JURY_ORDER if is_agent_enabled(aid)]
    if not state:
        return enabled
    roster = state.get("concept_roster")
    if isinstance(roster, list) and roster:
        wanted = {str(x) for x in roster}
        picked = [aid for aid in enabled if aid in wanted]
        if picked:
            return picked
    return enabled


def clamp_grade(raw: Any) -> int:
    try:
        g = int(round(float(raw)))
    except (TypeError, ValueError):
        g = 4
    return max(1, min(6, g))


def summarize_round(grades: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [clamp_grade(g.get("grade")) for g in grades if isinstance(g, dict)]
    if not vals:
        return {
            "average": None,
            "has_mangelhaft": False,
            "presentable": False,
            "passed": False,
            "count": 0,
        }
    average = sum(vals) / len(vals)
    has_mangelhaft = any(v >= MANGELHAFT_MIN for v in vals)
    presentable = not has_mangelhaft
    return {
        "average": round(average, 2),
        "has_mangelhaft": has_mangelhaft,
        "presentable": presentable,
        "passed": presentable,
        "count": len(vals),
        "grades": vals,
    }


def start_panel_round(state: dict[str, Any]) -> dict[str, Any]:
    """Startet eine neue Jury-Runde (Noten der Runde werden geleert)."""
    jury = jury_agents(state)
    prev = int(state.get("concept_panel_round") or 0)
    round_no = prev + 1
    return {
        "concept_panel_round": round_no,
        "concept_panel_queue": list(jury),
        "concept_panel_grades": [],
        "concept_panel_average": None,
        "concept_panel_passed": False,
        "concept_panel_forced": False,
        "concept_panel_done": False,
        "concept_panel_reverted": False,
        "panel_reviewer_id": jury[0] if jury else None,
    }


def next_reviewer_updates(queue: list[str]) -> dict[str, Any]:
    remaining = [str(x) for x in queue if str(x).strip()]
    if not remaining:
        return {"concept_panel_queue": [], "panel_reviewer_id": None}
    return {
        "concept_panel_queue": remaining,
        "panel_reviewer_id": remaining[0],
    }


def pop_reviewer(queue: list[str], agent_id: str) -> list[str]:
    out = [str(x) for x in queue if str(x).strip()]
    if out and out[0] == agent_id:
        return out[1:]
    return [x for x in out if x != agent_id]


def snapshot_concept(
    state: dict[str, Any],
    *,
    round_no: int,
    summary: dict[str, Any],
    grades: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {key: copy.deepcopy(state.get(key)) for key in _CONCEPT_SNAPSHOT_KEYS}
    return {
        "round": round_no,
        "average": summary.get("average"),
        "has_mangelhaft": bool(summary.get("has_mangelhaft")),
        "grades": copy.deepcopy(grades),
        "payload": payload,
    }


def restore_snapshot_payload(snap: dict[str, Any]) -> dict[str, Any]:
    payload = snap.get("payload") if isinstance(snap.get("payload"), dict) else {}
    updates = copy.deepcopy(payload)
    updates["concept_panel_grades"] = copy.deepcopy(snap.get("grades") or [])
    updates["concept_panel_average"] = snap.get("average")
    return updates


def last_snapshot(state: dict[str, Any]) -> dict[str, Any] | None:
    history = state.get("concept_panel_history") or []
    if not isinstance(history, list) or not history:
        return None
    prev = history[-1]
    return prev if isinstance(prev, dict) else None


def average_worsened(new_avg: Any, old_avg: Any) -> bool:
    if new_avg is None or old_avg is None:
        return False
    try:
        return float(new_avg) > float(old_avg) + 0.001
    except (TypeError, ValueError):
        return False


def build_revision_feedback(
    grades: list[dict[str, Any]],
    *,
    average: float | None,
    previous_average: float | None = None,
    reverted: bool = False,
    max_rounds_reached: bool = False,
) -> str:
    lines = [
        "Jury-Noten optimieren: Korrektur gemäß Nutzer- und Jury-Feedback.",
        "Bei Nutzer-Rückmeldung zu Grundriss/Raum: Platzierung und Proportionen dürfen angepasst werden.",
        "Hartes Kriterium: keine Note 5 oder 6. Es gibt keinen Pflicht-Schnitt von 2,0.",
        f"Aktueller Schnitt: {average if average is not None else 'n/a'}"
        + (f" (vorher {previous_average})" if previous_average is not None else "")
        + ".",
    ]
    if reverted:
        lines.append(
            "Die letzte Fassung hat den Schnitt verschlechtert – zurück zur vorherigen. "
            "Optimiere von dort, ohne bereits bessere Aspekte zu verderben."
        )
    if max_rounds_reached:
        lines.append(
            f"Rundenbudget ({MAX_PANEL_ROUNDS}) ist aufgebraucht. "
            "Nur noch Note 5/6 beseitigen; Rest nicht verschlechtern."
        )
    lines.append("")
    for g in grades:
        if not isinstance(g, dict):
            continue
        aid = str(g.get("agent_id") or "?")
        grade = clamp_grade(g.get("grade"))
        marker = " ← BLOCKER" if grade >= MANGELHAFT_MIN else ""
        lines.append(f"- {aid}: Note {grade} ({grade_label(grade)}){marker}")
        verdict = str(g.get("verdict") or "").strip()
        if verdict:
            lines.append(f"  Urteil: {verdict}")
        for issue in (g.get("issues") or [])[:4]:
            lines.append(f"  Problem: {issue}")
        imp = str(g.get("improvement") or "").strip()
        if imp:
            lines.append(f"  Verbesserung: {imp}")
    lines.append("")
    lines.append(
        "Arbeite die Kritik verbindlich ein. Halte Frozen Design Spec / Außenmaße ein. "
        "Priorität: schlechteste Noten anheben."
    )
    return "\n".join(lines)


def panel_status_message(
    summary: dict[str, Any],
    *,
    round_no: int,
    reverted: bool = False,
    presenting: bool = False,
) -> str:
    avg = summary.get("average")
    if reverted and presenting:
        return (
            f"Jury-Runde {round_no}: Schnitt hat sich verschlechtert → "
            f"vorherige Fassung (Schnitt {avg}) zur User-Entscheidung."
        )
    if summary.get("has_mangelhaft"):
        return (
            f"Jury-Runde {round_no}: Schnitt {avg}, mindestens Note ≥5 "
            f"– Konzept zur User-Entscheidung."
        )
    return f"Jury-Runde {round_no}: Schnitt {avg} – Konzept zur User-Entscheidung."


def decide_completed_round(
    state: dict[str, Any],
    *,
    summary: dict[str, Any],
    grades: list[dict[str, Any]],
    round_no: int,
) -> dict[str, Any]:
    """Wertet eine abgeschlossene Jury-Runde aus – immer User-Vorschau, kein Auto-Rebuild."""
    history = list(state.get("concept_panel_history") or [])
    history.append(snapshot_concept(state, round_no=round_no, summary=summary, grades=grades))
    msg = panel_status_message(summary, round_no=round_no, presenting=True)
    updates: dict[str, Any] = {
        "concept_panel_history": history,
        "concept_panel_reverted": False,
        "concept_panel_average": summary.get("average"),
        "concept_panel_grades": grades,
        **_present_flags(summary.get("average")),
    }
    return {"action": "present", "message": msg, "updates": updates}


def _present_flags(average: Any) -> dict[str, Any]:
    return {
        "concept_panel_passed": True,
        "concept_panel_forced": False,
        "concept_panel_done": True,
        "concept_panel_awaiting_rebuild": False,
        "human_approval_required": True,
        "escalation_reason": "concept_approval",
        "refinement_request": None,
        "concept_panel_queue": [],
        "panel_reviewer_id": None,
        "concept_panel_average": average,
    }


def _revise_flags(
    *,
    grades: list[dict[str, Any]],
    average: Any,
    previous_average: Any,
    reverted: bool,
    round_no: int,
) -> dict[str, Any]:
    feedback = build_revision_feedback(
        grades,
        average=average if isinstance(average, (int, float)) else None,
        previous_average=previous_average if isinstance(previous_average, (int, float)) else None,
        reverted=reverted,
        max_rounds_reached=round_no >= MAX_PANEL_ROUNDS,
    )
    return {
        "concept_panel_passed": False,
        "concept_panel_forced": False,
        "concept_panel_done": False,
        "concept_panel_awaiting_rebuild": True,
        "concept_panel_queue": [],
        "panel_reviewer_id": None,
        "human_approval_required": False,
        "escalation_reason": None,
        "concept_critiqued": False,
        "refinement_request": {
            "from_agent": "supervisor",
            "target": "concept_builder",
            "reason": "concept_feedback",
            "feedback": feedback,
        },
    }
