"""Konzept-Jury: Schulnoten-Konsens vor User-Freigabe.

Regeln:
- Jury bewertet nacheinander (1–6, 1 = best).
- Mangelhaft = Note >= 5.
- Freigabe nur wenn aktuelle Runde: kein Mangelhaft und Mittelwert <= 2.0.
- Version B: nur die letzte Runde zählt; Fail → Concept Builder → neue Jury-Runde.
- Max. 3 Runden, danach Force an den User.
"""

from __future__ import annotations

from typing import Any

from app.services.agent_workflow_store import is_agent_enabled

JURY_ORDER = (
    "flexible_specialist",
    "interior_architect",
    "vv_manager",
    "concept_critic",
    "fertigung_specialist",
)

MAX_PANEL_ROUNDS = 3
PASS_AVERAGE_MAX = 2.0
MANGELHAFT_MIN = 5

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


def jury_agents() -> list[str]:
    return [aid for aid in JURY_ORDER if is_agent_enabled(aid)]


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
            "passed": False,
            "count": 0,
        }
    average = sum(vals) / len(vals)
    has_mangelhaft = any(v >= MANGELHAFT_MIN for v in vals)
    passed = (not has_mangelhaft) and average <= PASS_AVERAGE_MAX
    return {
        "average": round(average, 2),
        "has_mangelhaft": has_mangelhaft,
        "passed": passed,
        "count": len(vals),
        "grades": vals,
    }


def start_panel_round(state: dict[str, Any]) -> dict[str, Any]:
    """Startet eine neue Jury-Runde (Noten der Runde werden geleert)."""
    jury = jury_agents()
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


def build_revision_feedback(grades: list[dict[str, Any]], *, average: float | None) -> str:
    lines = [
        "Jury-Konsens: Konzept muss überarbeitet werden (nur aktuelle Runde zählt).",
        f"Durchschnitt: {average if average is not None else 'n/a'} (Ziel ≤ {PASS_AVERAGE_MAX}, kein Mangelhaft).",
        "",
    ]
    for g in grades:
        if not isinstance(g, dict):
            continue
        aid = str(g.get("agent_id") or "?")
        grade = clamp_grade(g.get("grade"))
        lines.append(f"- {aid}: Note {grade} ({grade_label(grade)})")
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
        "Kein neues Alternativ-Konzept, sondern gezielte Korrektur."
    )
    return "\n".join(lines)


def panel_status_message(summary: dict[str, Any], *, round_no: int, forced: bool = False) -> str:
    avg = summary.get("average")
    if forced:
        return (
            f"Jury-Runde {round_no}/{MAX_PANEL_ROUNDS}: Max-Runden erreicht "
            f"(Schnitt {avg}, Mangelhaft={summary.get('has_mangelhaft')}) "
            "→ Konzept trotz Restrisiko zur User-Freigabe."
        )
    if summary.get("passed"):
        return (
            f"Jury-Runde {round_no}: bestanden "
            f"(Schnitt {avg} ≤ {PASS_AVERAGE_MAX}, kein Mangelhaft) → User-Freigabe."
        )
    return (
        f"Jury-Runde {round_no}: nicht bestanden "
        f"(Schnitt {avg}, Mangelhaft={summary.get('has_mangelhaft')}) → Überarbeitung."
    )
