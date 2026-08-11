"""Master / Supervisor Agent – Orchestrator inkl. V&V-/Flexible-/Fertigungspfad."""

import logging

from agents.state import AgentState
from agents.transcript import make_entry
from app.services.agent_workflow_store import get_max_iterations

logger = logging.getLogger(__name__)

_USER_ESCALATION_REASONS = frozenset({"concept_approval", "requirements_approval"})


def _advance_to_next_part(state: AgentState) -> AgentState | None:
    """Schließt das aktuelle Teil ab und rückt zum nächsten vor."""
    sandbox_result = state.get("sandbox_result")
    if not sandbox_result or sandbox_result.get("status") != "SUCCESS":
        return None

    parts = (state.get("requirements_contract") or {}).get("parts", [])
    idx = state.get("current_part_index", 0)
    completed = list(state.get("completed_parts", []))
    part_name = parts[idx].get("name", f"Teil {idx + 1}") if idx < len(parts) else f"Teil {idx + 1}"

    if idx < len(parts):
        completed.append(
            {
                "name": part_name,
                "part_contract": parts[idx],
                "stock_and_tool_context": state.get("stock_and_tool_context"),
                "generated_code": state.get("generated_code"),
                "sandbox_result": sandbox_result,
                "manufacturing_plan": state.get("manufacturing_plan"),
            }
        )

    logger.info("Supervisor: Teil %s/%s abgeschlossen.", idx + 1, len(parts))
    next_idx = idx + 1
    next_hint = (
        f"Nächstes Teil: {parts[next_idx].get('name')}"
        if next_idx < len(parts)
        else "Alle Teile fertig → Montage-Prüfung"
    )

    return {
        "completed_parts": completed,
        "current_part_index": next_idx,
        "stock_and_tool_context": None,
        "generated_code": None,
        "sandbox_result": None,
        "manufacturing_plan": None,
        "manufacturing_assessed": False,
        "manufacturing_feasibility": None,
        "error_history": [],
        "iteration_count": 0,
        "current_agent": "supervisor",
        "messages": [
            {
                "role": "assistant",
                "content": f"[Supervisor] Teil {idx + 1}/{len(parts)} („{part_name}“) abgeschlossen. {next_hint}",
            }
        ],
        "agent_transcript": [
            make_entry(
                "supervisor",
                "part_completed",
                f"Teil {idx + 1}/{len(parts)} „{part_name}“ in completed_parts gesichert. {next_hint}",
                detail={
                    "completed_index": idx,
                    "part_name": part_name,
                    "export_paths": sandbox_result.get("export_paths"),
                    "remaining_parts": max(len(parts) - next_idx, 0),
                },
                to_agent="inventory_manager" if next_idx < len(parts) else "montage_manager",
            )
        ],
    }


def _compute_next_route(state: AgentState) -> str:
    """Gemeinsame Routing-Logik für Node-Preview und Conditional Edges."""
    reason = state.get("escalation_reason")
    if state.get("human_approval_required") and reason in _USER_ESCALATION_REASONS:
        return "human_escalation"

    refinement = state.get("refinement_request")
    if refinement:
        target = refinement.get("target") or "concept_builder"
        # Unbekannte Targets auf bekannte Nodes mappen
        allowed = {
            "flexible_specialist",
            "vv_manager",
            "concept_builder",
            "inventory_manager",
            "fertigung_specialist",
            "montage_manager",
            "builder_3d",
            "validator",
            "human_escalation",
        }
        return target if target in allowed else "concept_builder"

    # Requirements-Abstimmung vor weiterem Routing (sonst Loop auf vv_manager)
    if state.get("vv_needs_alignment") or (
        state.get("human_approval_required") and reason == "requirements_approval"
    ):
        return "human_escalation"

    # 1) Flexible Specialist früh beraten lassen
    if not state.get("flexible_consulted"):
        return "flexible_specialist"

    # 2) V&V high-level Requirements (Konzeptphase)
    consulted = list(state.get("vv_consulted_phases") or [])
    if "concept" not in consulted:
        return "vv_manager"

    # 3) Konzept
    if not state.get("requirements_contract"):
        return "concept_builder"

    if not state.get("concept_approved"):
        if reason == "concept_approval":
            return "human_escalation"
        return "concept_builder"

    # 4) Nach Konzept-Freigabe: V&V auf Design-Detail
    if "design" not in consulted:
        return "vv_manager"

    parts = (state.get("requirements_contract") or {}).get("parts", [])
    if parts and state.get("current_part_index", 0) >= len(parts):
        # Abschluss: Fertigungs-V&V → Montage-Prüfung → END
        if "manufacturing" not in consulted:
            return "vv_manager"
        if not state.get("montage_assessed"):
            return "montage_manager"
        return "END"

    # 5) Inventory Specialist
    if not state.get("stock_and_tool_context"):
        return "inventory_manager"

    # 6) Fertigungs Specialist
    if not state.get("manufacturing_assessed"):
        return "fertigung_specialist"

    # 7) Nach Fertigungsplan: V&V manufacturing-Detail
    if "manufacturing" not in consulted:
        return "vv_manager"

    # 8) 3D + Validator
    if not state.get("generated_code"):
        return "builder_3d"

    sandbox_result = state.get("sandbox_result")
    if not sandbox_result:
        return "validator"

    if sandbox_result.get("status") != "SUCCESS":
        return "builder_3d"

    return "inventory_manager"


def supervisor_node(state: AgentState) -> AgentState:
    """Zählt Iterationen, rückt bei Erfolg zum nächsten Teil vor, routet Pipeline."""

    advance_update = _advance_to_next_part(state)
    if advance_update is not None:
        return advance_update

    iteration_count = state.get("iteration_count", 0) + 1
    parts = (state.get("requirements_contract") or {}).get("parts", [])
    idx = state.get("current_part_index", 0)
    part_name = parts[idx].get("name") if 0 <= idx < len(parts) else None
    next_route = _compute_next_route(state)

    summary = (
        f"Iteration {iteration_count}"
        + (f", Teil {idx + 1}/{len(parts)}" if parts else "")
        + (f" („{part_name}“)" if part_name else "")
        + f" → Route: {next_route}"
    )

    updates: AgentState = {
        "iteration_count": iteration_count,
        "current_agent": "supervisor",
        "messages": [{"role": "assistant", "content": f"[Supervisor] {summary}"}],
        "agent_transcript": [
            make_entry(
                "supervisor",
                "route",
                summary,
                detail={
                    "iteration_count": iteration_count,
                    "current_part_index": idx,
                    "total_parts": len(parts),
                    "part_name": part_name,
                    "concept_approved": bool(state.get("concept_approved")),
                    "vv_phase": state.get("vv_phase"),
                    "flexible_consulted": bool(state.get("flexible_consulted")),
                    "has_contract": bool(state.get("requirements_contract")),
                    "has_stock_context": bool(state.get("stock_and_tool_context")),
                    "manufacturing_assessed": bool(state.get("manufacturing_assessed")),
                    "montage_assessed": bool(state.get("montage_assessed")),
                    "has_code": bool(state.get("generated_code")),
                    "has_sandbox": bool(state.get("sandbox_result")),
                    "refinement": state.get("refinement_request"),
                    "escalation_reason": state.get("escalation_reason"),
                    "next_route": next_route,
                },
                to_agent=next_route,
            )
        ],
    }

    if not state.get("human_approval_required") and iteration_count > get_max_iterations():
        logger.warning(
            "Supervisor: Iterationslimit (%s) überschritten – fahre ohne User-Pause fort (Iteration %s)",
            get_max_iterations(),
            iteration_count,
        )
        updates["agent_transcript"] = list(updates.get("agent_transcript") or []) + [
            make_entry(
                "supervisor",
                "max_iterations_continue",
                (
                    f"Iterationslimit ({get_max_iterations()}) überschritten – "
                    "keine User-Eskalation, Pipeline arbeitet weiter."
                ),
                detail={"iteration_count": iteration_count, "next_route": next_route},
                to_agent=next_route,
            )
        ]

    # Veraltete Generic-Eskalations-Flags verwerfen (Konzept + Requirements bleiben)
    if state.get("human_approval_required") and state.get("escalation_reason") not in _USER_ESCALATION_REASONS:
        updates["human_approval_required"] = False
        updates["escalation_reason"] = None

    sandbox = state.get("sandbox_result")
    if sandbox and sandbox.get("status") != "SUCCESS" and next_route == "builder_3d":
        updates["sandbox_result"] = None
        updates["generated_code"] = None
        if not state.get("refinement_request"):
            updates["refinement_request"] = {
                "from_agent": "supervisor",
                "target": "builder_3d",
                "reason": "retry_after_sandbox_failure",
            }

    return updates


def route_from_supervisor(state: AgentState) -> str:
    return _compute_next_route(state)
