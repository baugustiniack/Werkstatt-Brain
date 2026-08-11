"""Flexible Specialist – vom Supervisor zugeschnittenes Expertenprofil.

Berät von Anfang an Concept Builder, V&V, Inventory und Fertigung und schreibt
kurze Advisory-Notes in den State.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_PROFILE_SYSTEM = """Du bist der Supervisor-Assistent und definierst einen Flexible Specialist
für eine Werkstatt-Anfrage (CNC, Holz, Möbel, Inventar). Antworte NUR mit JSON:
{
  "title": string,
  "expertise_domains": [string, ...],
  "focus": string,
  "constraints": [string, ...],
  "advice_priorities": [string, ...]
}
Das Profil muss spezifisch zur Anfrage sein (nicht generisch „Holzexperte“).
"""

_ADVICE_SYSTEM = """Du bist der Flexible Specialist mit dem gegebenen Profil.
Berate kurz die anderen Agenten (V&V, Concept, Inventory, Fertigung). Antworte NUR mit JSON:
{
  "advisory_notes": string,
  "risks": [string, ...],
  "suggestions_for_vv": [string, ...],
  "suggestions_for_concept": [string, ...],
  "suggestions_for_manufacturing": [string, ...]
}
Deutsch, konkret, max. 1 Seite Äquivalent.
"""


def _heuristic_profile(prompt: str) -> dict[str, Any]:
    low = prompt.lower()
    domains = ["Holzverarbeitung", "CNC-Fräsen"]
    if any(k in low for k in ("schrank", "möbel", "regal", "küche")):
        domains.append("Möbelbau")
    if any(k in low for k in ("ecke", "raum", "zimmer")):
        domains.append("Raumplanung / Einbaumöbel")
    if any(k in low for k in ("alu", "metall")):
        domains.append("Leichtmetall / Platten")
    return {
        "title": "Flexible Specialist (anfragespezifisch)",
        "expertise_domains": domains,
        "focus": (prompt.strip().split("\n")[0] or "Werkstück")[:160],
        "constraints": ["Vorhandene Werkstatt-Maschinen und Lagerware priorisieren"],
        "advice_priorities": ["Machbarkeit", "Materialeffizienz", "CNC-gerechte Geometrie"],
    }


def _ensure_profile(state: AgentState) -> dict[str, Any]:
    existing = state.get("flexible_specialist_profile")
    if isinstance(existing, dict) and existing.get("title"):
        return existing

    prompt = state.get("user_prompt") or ""
    guidance = get_agent_guidance("supervisor")
    if is_llm_configured():
        try:
            profile = call_llm_json(
                _PROFILE_SYSTEM,
                f"User-Prompt:\n{prompt}\n\nSupervisor-Guidance: {guidance or '(keine)'}",
                max_tokens=900,
            )
            if isinstance(profile, dict) and profile.get("title"):
                return profile
        except Exception as exc:  # noqa: BLE001
            logger.warning("Flexible-Profil LLM fehlgeschlagen: %s", exc)
    return _heuristic_profile(prompt)


def flexible_specialist_node(state: AgentState) -> AgentState:
    profile = _ensure_profile(state)
    prompt = state.get("user_prompt") or ""
    vv = state.get("vv_requirements") or {}
    contract = state.get("requirements_contract") or {}
    guidance = get_agent_guidance("flexible_specialist")

    advice: dict[str, Any]
    if is_llm_configured():
        try:
            advice = call_llm_json(
                _ADVICE_SYSTEM,
                (
                    f"Profil: {profile}\n"
                    f"Prompt: {prompt}\n"
                    f"V&V: {vv}\n"
                    f"Contract-Titel: {contract.get('project_title')}\n"
                    f"Guidance: {guidance or '(keine)'}"
                ),
                max_tokens=1400,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Flexible Advice LLM fehlgeschlagen: %s", exc)
            advice = {
                "advisory_notes": (
                    f"Als {profile.get('title')} priorisiere ich: "
                    + ", ".join(profile.get("advice_priorities") or ["Machbarkeit"])
                    + "."
                ),
                "risks": ["Unklare Maße – im Konzept mit Annahmen arbeiten"],
                "suggestions_for_vv": ["High-Level-Must-Requirements zuerst absichern"],
                "suggestions_for_concept": ["CNC-fertigbare Einzelteile statt Raumvolumen"],
                "suggestions_for_manufacturing": ["Maschinen aus Inventar früh prüfen"],
            }
    else:
        advice = {
            "advisory_notes": (
                f"Profil „{profile.get('title')}“ aktiv. Fokus: {profile.get('focus')}. "
                "Ich rate zu klaren Must-Requirements und inventartauglicher Geometrie."
            ),
            "risks": [],
            "suggestions_for_vv": ["Must/Should trennen"],
            "suggestions_for_concept": list(profile.get("expertise_domains") or [])[:3],
            "suggestions_for_manufacturing": ["Maschinen-Matching vor 3D-Code"],
        }

    notes = str(advice.get("advisory_notes") or "").strip()
    note = f"Profil „{profile.get('title')}“ – Beratung für V&V/Concept/Fertigung bereit."

    return {
        "flexible_specialist_profile": profile,
        "advisory_notes": notes,
        "flexible_advice": advice,
        "flexible_consulted": True,
        "refinement_request": None,
        "current_agent": "flexible_specialist",
        "messages": [{"role": "assistant", "content": f"[Flexible Specialist] {note}"}],
        "agent_transcript": [
            make_entry(
                "flexible_specialist",
                "advise",
                note,
                detail={
                    "profile": profile,
                    "advisory_notes": truncate(notes, 600),
                    "risks": advice.get("risks"),
                    "suggestions_for_vv": advice.get("suggestions_for_vv"),
                    "suggestions_for_concept": advice.get("suggestions_for_concept"),
                },
                to_agent="supervisor",
            )
        ],
    }
