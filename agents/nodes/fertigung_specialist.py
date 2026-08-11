"""Fertigungs Specialist – Maschinen-Expertise aus der Inventar-DB.

Bewertet Machbarkeit von Konzepten, wählt benötigte Maschinen und erstellt
Schritt-für-Schritt-Arbeitsabläufe für die Werkstatt.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.llm_client import call_llm_json, is_llm_configured
from agents.state import AgentState
from agents.transcript import make_entry, truncate
from app.services.agent_workflow_store import get_agent_guidance

logger = logging.getLogger(__name__)

_MFG_SYSTEM = """Du bist Fertigungs-Specialist einer CNC-/Holzwerkstatt.
Dir werden Inventar-Maschinen/Assets und ein Design-Konzept gegeben.
Antworte NUR mit JSON:
{
  "feasible": boolean,
  "summary": string,
  "machines_needed": [{"name": string, "role": string, "from_inventory": boolean}],
  "gaps": [string, ...],
  "work_steps": [{"step": number, "action": string, "machine": string | null, "notes": string}],
  "estimated_complexity": "low" | "medium" | "high"
}
Deutsch, praxisnah, realistisch zur Werkstatt.
"""


def _load_machine_inventory() -> list[dict[str, Any]]:
    """Liest Werkzeuge/Assets, die als Maschinen/Equipment taugen."""
    machines: list[dict[str, Any]] = []
    try:
        from app.db.postgres import SessionLocal
        from app.models.tool import Tool
        from app.models.unprocessed_asset import UnprocessedAsset

        db = SessionLocal()
        try:
            for tool in db.query(Tool).limit(80).all():
                machines.append(
                    {
                        "kind": "tool",
                        "name": tool.name,
                        "diameter_mm": float(tool.diameter_mm) if tool.diameter_mm is not None else None,
                        "status": getattr(tool.status, "value", str(tool.status)),
                    }
                )
            assets = (
                db.query(UnprocessedAsset)
                .order_by(UnprocessedAsset.discovered_at.desc())
                .limit(60)
                .all()
            )
            for asset in assets:
                tags = asset.tags or []
                title = (asset.title or asset.file_path or "")[:120]
                blob = f"{title} {' '.join(tags)} {(asset.notes or '')[:200]}".lower()
                if any(
                    k in blob
                    for k in (
                        "cnc",
                        "fräs",
                        "fraes",
                        "säge",
                        "saege",
                        "maschine",
                        "spindel",
                        "drucker",
                        "laser",
                        "schleif",
                        "presse",
                    )
                ) or "maschine" in tags or "machine" in tags:
                    machines.append(
                        {
                            "kind": "asset",
                            "name": title or str(asset.id)[:8],
                            "file_type": asset.file_type.value if asset.file_type else None,
                            "tags": tags[:8],
                            "notes": (asset.notes or "")[:300],
                        }
                    )
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Maschinen-Inventar konnte nicht geladen werden: %s", exc)
    return machines


def _heuristic_plan(
    contract: dict[str, Any],
    machines: list[dict[str, Any]],
    advisory: str,
) -> dict[str, Any]:
    title = contract.get("project_title") or "Bauteil"
    tool_names = [m.get("name") for m in machines if m.get("kind") == "tool"][:5]
    asset_names = [m.get("name") for m in machines if m.get("kind") == "asset"][:5]
    primary = asset_names[0] if asset_names else (tool_names[0] if tool_names else "CNC-Fräse (angenommen)")
    steps = [
        {"step": 1, "action": "Material zusägen / zuschnitt nach Konzeptmaßen", "machine": primary, "notes": ""},
        {"step": 2, "action": f"CNC-Fräsen der Teile für „{title}“", "machine": primary, "notes": "Werkzeug aus Inventar wählen"},
        {"step": 3, "action": "Entgraten / Schleifen", "machine": None, "notes": "Handarbeit oder Schleifmaschine"},
        {"step": 4, "action": "Probeaufbau / Passung prüfen", "machine": None, "notes": "Abnahme laut V&V"},
        {"step": 5, "action": "Endmontage / Oberflächenbehandlung", "machine": None, "notes": ""},
    ]
    return {
        "feasible": True,
        "summary": (
            f"Vorläufige Fertigungsbewertung für „{title}“. "
            f"{len(machines)} Inventar-Einträge berücksichtigt."
            + (f" Advisory: {advisory[:160]}" if advisory else "")
        ),
        "machines_needed": (
            [{"name": primary, "role": "Hauptbearbeitung", "from_inventory": bool(asset_names or tool_names)}]
            + [{"name": n, "role": "Werkzeug", "from_inventory": True} for n in tool_names[:2]]
        ),
        "gaps": [] if machines else ["Keine Maschinen/Assets in der Inventar-DB gefunden – Annahmen treffen."],
        "work_steps": steps,
        "estimated_complexity": "medium",
    }


def fertigung_specialist_node(state: AgentState) -> AgentState:
    contract = state.get("requirements_contract") or {}
    stock = state.get("stock_and_tool_context") or {}
    vv = state.get("vv_requirements") or {}
    advisory = state.get("advisory_notes") or ""
    guidance = get_agent_guidance("fertigung_specialist")
    machines = _load_machine_inventory()

    if is_llm_configured():
        try:
            plan = call_llm_json(
                _MFG_SYSTEM,
                (
                    f"Konzept: {contract}\n"
                    f"Inventar-Kontext: {stock}\n"
                    f"V&V: {vv}\n"
                    f"Advisory: {advisory}\n"
                    f"Maschinen/Assets aus DB: {machines[:40]}\n"
                    f"Guidance: {guidance or '(keine)'}"
                ),
                max_tokens=2200,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fertigung LLM fehlgeschlagen: %s", exc)
            plan = _heuristic_plan(contract, machines, advisory if isinstance(advisory, str) else "")
    else:
        plan = _heuristic_plan(contract, machines, advisory if isinstance(advisory, str) else "")

    feasible = bool(plan.get("feasible", True))
    steps = plan.get("work_steps") or []
    note = (
        f"Fertigung {'möglich' if feasible else 'kritisch'}: "
        f"{len(plan.get('machines_needed') or [])} Maschinen, {len(steps)} Arbeitsschritte."
    )

    return {
        "manufacturing_plan": plan,
        "manufacturing_feasibility": {
            "feasible": feasible,
            "gaps": plan.get("gaps") or [],
            "estimated_complexity": plan.get("estimated_complexity"),
        },
        "manufacturing_assessed": True,
        "refinement_request": None,
        "current_agent": "fertigung_specialist",
        "messages": [{"role": "assistant", "content": f"[Fertigungs Specialist] {note}"}],
        "agent_transcript": [
            make_entry(
                "fertigung_specialist",
                "manufacturing_plan",
                note,
                detail={
                    "feasible": feasible,
                    "machines_needed": plan.get("machines_needed"),
                    "work_steps_count": len(steps),
                    "summary": truncate(str(plan.get("summary") or ""), 500),
                    "inventory_machines_seen": len(machines),
                    "gaps": plan.get("gaps"),
                },
                to_agent="supervisor",
            )
        ],
    }
