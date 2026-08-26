"""Supervisor-Roster: welche Agenten an der Konzeptbildung teilnehmen.

Komplexität bestimmt Intake (Flex/Interior) und Jury-Zusammensetzung.
V&V + Concept Builder + Critic sind Kern; Spezialisten nur bei Bedarf.
Lageplan-/Einrichtungs-Anfragen: Interior + V&V + Critic – kein Flex/Fertigung.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.services.agent_workflow_store import is_agent_enabled

Complexity = Literal["low", "medium", "high"]

_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}

# Feste Reihenfolge – Schnittmenge mit enabled + Roster
ROSTER_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "low": ("vv_manager", "concept_critic"),
    "medium": ("flexible_specialist", "vv_manager", "concept_critic"),
    "high": (
        "flexible_specialist",
        "interior_architect",
        "vv_manager",
        "concept_critic",
        "fertigung_specialist",
    ),
    # Raum-/Lageplan ohne CNC-Auftrag: keine Werkstatt-/Fertigungs-Jury
    "layout": (
        "interior_architect",
        "vv_manager",
        "concept_critic",
    ),
}

_ROOM_RE = re.compile(
    r"zimmer|raum|ecke|wohn|küche|kueche|schlaf|büro|buero|flur|"
    r"einbau|grundriss|innenraum|interior|room|apartment|wohnung|"
    r"floorplan|aufstell|lageplan|einricht|möblier|moebli|platzier",
    re.IGNORECASE,
)
_LAYOUT_INTENT_RE = re.compile(
    r"grundriss|lageplan|floor\s*plan|einricht(?:ung|ungskonzept)|"
    r"möblier|moebli|wo\s+was\s+steht|raumplan|platzier(?:ung|en)|"
    r"inneneinrichtung|aufstellplan",
    re.IGNORECASE,
)
_MANUFACTURING_INTENT_RE = re.compile(
    r"\bcnc\b|fräs|fraes|zuschnitt|fertigen|fertigung|bauen\b|herstell|"
    r"\bstl\b|\bstep\b|3d[\s-]?modell|cad[\s-]?code|plattenzuschnitt|"
    r"werkstattauftrag|maschine",
    re.IGNORECASE,
)
_CNC_RE = re.compile(
    r"cnc|fräs|fraes|lochreihe|raster|32er|"
    r"multiplex|mdf|spanplatte|eiche|material|toleranz",
    re.IGNORECASE,
)
_FURNITURE_RE = re.compile(
    r"schrank|regal|korpus|garderobe|sideboard|kommode|einlegeboden|möbel|moebel",
    re.IGNORECASE,
)
_DIM_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?(?:\s*(?:x|×)\s*\d+(?:[.,]\d+)?)?\s*(?:mm|cm|m)\b",
    re.IGNORECASE,
)
_LARGE_MM_RE = re.compile(r"\b([12]\d{3}|[3-9]\d{3})\s*mm\b", re.IGNORECASE)


def _prompt_blob(state: dict[str, Any]) -> str:
    parts = [str(state.get("user_prompt") or "")]
    vv = state.get("vv_requirements") if isinstance(state.get("vv_requirements"), dict) else {}
    if vv.get("title"):
        parts.append(str(vv.get("title")))
    if vv.get("summary"):
        parts.append(str(vv.get("summary")))
    return "\n".join(parts)


def _is_layout_only_request(blob: str) -> bool:
    """Grundriss/Lageplan/Einrichtung ohne expliziten CNC-/Fertigungsauftrag."""
    if not _LAYOUT_INTENT_RE.search(blob):
        return False
    if _MANUFACTURING_INTENT_RE.search(blob):
        return False
    return True


def _part_count(state: dict[str, Any]) -> int:
    contract = state.get("requirements_contract") if isinstance(state.get("requirements_contract"), dict) else {}
    parts = contract.get("parts") or []
    return len(parts) if isinstance(parts, list) else 0


def _has_refs(state: dict[str, Any]) -> bool:
    ids = state.get("reference_asset_ids") or []
    if isinstance(ids, list) and ids:
        return True
    prompt = str(state.get("user_prompt") or "")
    return "id=" in prompt.lower() or "/items/" in prompt.lower()


def roster_for_level(level: str) -> list[str]:
    wanted = ROSTER_BY_LEVEL.get(level) or ROSTER_BY_LEVEL["medium"]
    return [aid for aid in wanted if is_agent_enabled(aid)]


def filter_roster(agent_ids: list[str] | None) -> list[str]:
    """Nutzer-/State-Roster: nur erlaubte + aktivierte Agenten, stabile Reihenfolge."""
    from app.services.settings_store import CONCEPT_ROSTER_SELECTABLE

    allowed = set(CONCEPT_ROSTER_SELECTABLE)
    wanted = [str(a).strip() for a in (agent_ids or []) if str(a).strip()]
    order = {aid: i for i, aid in enumerate(CONCEPT_ROSTER_SELECTABLE)}
    picked = [aid for aid in wanted if aid in allowed and is_agent_enabled(aid)]
    return sorted(picked, key=lambda x: order.get(x, 99))


def apply_concept_roster_policy(state: dict[str, Any], assessed: dict[str, Any]) -> dict[str, Any]:
    """Manual-Roster aus State/Settings anwenden, sonst Supervisor-Heuristik."""
    from app.services.settings_store import (
        resolve_concept_roster_mode,
        resolve_manual_concept_roster,
    )

    mode = str(state.get("concept_roster_mode") or resolve_concept_roster_mode() or "auto").lower()
    if mode == "manual":
        roster = filter_roster(state.get("concept_roster") or resolve_manual_concept_roster())
        if not roster:
            roster = filter_roster(list(resolve_manual_concept_roster()))
        return {
            **assessed,
            "concept_roster": roster,
            "concept_roster_mode": "manual",
            "concept_roster_source": "manual",
            "concept_complexity_reasons": [
                "Manuelle Agenten-Auswahl",
                *(assessed.get("concept_complexity_reasons") or []),
            ][:8],
        }
    roster = filter_roster(assessed.get("concept_roster")) or roster_for_level(
        str(assessed.get("concept_complexity") or "medium")
    )
    return {
        **assessed,
        "concept_roster": roster,
        "concept_roster_mode": "auto",
        "concept_roster_source": "supervisor",
    }


def assess_concept_complexity(state: dict[str, Any]) -> dict[str, Any]:
    """Heuristik: low / medium / high + Begründung; Layout-only → Roster ohne Flex/Fertigung."""
    blob = _prompt_blob(state)
    n_parts = _part_count(state)
    refs = _has_refs(state)
    reasons: list[str] = []
    score = 0
    layout_only = _is_layout_only_request(blob)

    if _ROOM_RE.search(blob):
        score += 3
        reasons.append("Raum-/Einbau-Kontext")
    if refs:
        score += 3
        reasons.append("Referenzfotos/Grundriss")
    if n_parts >= 8:
        score += 3
        reasons.append(f"{n_parts} Teile")
    elif n_parts >= 3:
        score += 2
        reasons.append(f"{n_parts} Teile")
    elif n_parts == 2:
        score += 1
        reasons.append("zwei Teile")

    if _FURNITURE_RE.search(blob):
        score += 2
        reasons.append("Möbel-/Korpus-Konzept")
    # CNC-Punkte nur, wenn wirklich Fertigung gemeint ist – nicht nur „PAX mit Schiebetüren“
    if not layout_only and _CNC_RE.search(blob):
        score += 2
        reasons.append("CNC-/Material-Details")
    if _DIM_RE.search(blob) or _LARGE_MM_RE.search(blob):
        score += 1
        reasons.append("explizite Maße")
    if not layout_only and re.search(
        r"schiebetür|schiebetuer|mittelwand|fachboden", blob, re.IGNORECASE
    ):
        score += 2
        reasons.append("Schiebetür/Innenausbau")

    if layout_only:
        reasons.insert(0, "Lageplan-/Einrichtungsanfrage (ohne Fertigungsauftrag)")
        roster = roster_for_level("layout")
        level: Complexity = "high" if score >= 5 else "medium" if score >= 2 else "low"
        return apply_concept_roster_policy(
            state,
            {
                "concept_complexity": level,
                "concept_roster": roster,
                "concept_complexity_reasons": reasons[:8],
                "concept_complexity_score": score,
                "concept_mode": "layout",
            },
        )

    if score >= 5:
        level = "high"
    elif score >= 2:
        level = "medium"
    else:
        level = "low"
        if not reasons:
            reasons.append("einfaches Einzelkonzept ohne Raumbezug")

    roster = roster_for_level(level)
    assessed = {
        "concept_complexity": level,
        "concept_roster": roster,
        "concept_complexity_reasons": reasons[:8],
        "concept_complexity_score": score,
        "concept_mode": "manufacturing",
    }
    return apply_concept_roster_policy(state, assessed)


def merge_complexity(existing: dict[str, Any] | None, fresh: dict[str, Any]) -> dict[str, Any]:
    """Komplexität darf nur steigen (nach Contract/Fotos), nicht fallen.

    Layout-Roster bleibt erhalten, solange der Auftrag Layout-only ist.
    """
    if not existing or not existing.get("concept_complexity"):
        return fresh

    if str(existing.get("concept_roster_mode") or fresh.get("concept_roster_mode") or "") == "manual":
        reasons = list(existing.get("concept_complexity_reasons") or [])
        for r in fresh.get("concept_complexity_reasons") or []:
            if r not in reasons:
                reasons.append(r)
        roster = filter_roster(existing.get("concept_roster") or fresh.get("concept_roster"))
        return {
            **fresh,
            "concept_roster": roster,
            "concept_roster_mode": "manual",
            "concept_roster_source": "manual",
            "concept_complexity_reasons": reasons[:8],
        }

    # Layout-Modus: Roster nicht mit high+Fertigung überschreiben (nur bei Auto)
    if fresh.get("concept_mode") == "layout" or existing.get("concept_mode") == "layout":
        mode = "layout"
        old = str(existing.get("concept_complexity") or "low")
        new = str(fresh.get("concept_complexity") or "low")
        level = new if _LEVEL_RANK.get(new, 0) >= _LEVEL_RANK.get(old, 0) else old
        reasons = list(existing.get("concept_complexity_reasons") or [])
        for r in fresh.get("concept_complexity_reasons") or []:
            if r not in reasons:
                reasons.append(r)
        return {
            "concept_complexity": level,
            "concept_roster": roster_for_level("layout"),
            "concept_complexity_reasons": reasons[:8],
            "concept_complexity_score": max(
                int(existing.get("concept_complexity_score") or 0),
                int(fresh.get("concept_complexity_score") or 0),
            ),
            "concept_mode": mode,
        }

    old = str(existing.get("concept_complexity") or "low")
    new = str(fresh.get("concept_complexity") or "low")
    if _LEVEL_RANK.get(new, 0) > _LEVEL_RANK.get(old, 0):
        reasons = list(existing.get("concept_complexity_reasons") or [])
        for r in fresh.get("concept_complexity_reasons") or []:
            if r not in reasons:
                reasons.append(r)
        return {**fresh, "concept_complexity_reasons": reasons[:8]}
    return {
        "concept_complexity": old,
        "concept_roster": list(existing.get("concept_roster") or roster_for_level(old)),
        "concept_complexity_reasons": list(existing.get("concept_complexity_reasons") or []),
        "concept_complexity_score": existing.get("concept_complexity_score"),
        "concept_mode": existing.get("concept_mode") or fresh.get("concept_mode"),
    }


def agent_in_roster(state: dict[str, Any], agent_id: str) -> bool:
    roster = state.get("concept_roster")
    if not isinstance(roster, list) or not roster:
        return True
    return agent_id in roster
