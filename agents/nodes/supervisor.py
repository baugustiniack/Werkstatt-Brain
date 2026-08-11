"""Master / Supervisor Agent – Orchestrator inkl. V&V-/Flexible-/Fertigungspfad."""

import logging

from agents.state import AgentState
from agents.transcript import make_entry
from app.services.agent_workflow_store import get_max_iterations, is_agent_enabled

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


def _overlay_for_disabled_agents(state: AgentState) -> AgentState:
    """Virtueller State, damit deaktivierte optionale Agenten übersprungen werden."""
    overlay: AgentState = dict(state)
    # Flexible aus
    if not is_agent_enabled("flexible_specialist"):
        overlay["flexible_consulted"] = True
    # Leer-Agenten aus → als konsultiert markieren
    consulted_empty = list(overlay.get("empty_agents_consulted") or [])
    for aid in ("custom_agent_1", "custom_agent_2"):
        if not is_agent_enabled(aid) and aid not in consulted_empty:
            consulted_empty.append(aid)
    overlay["empty_agents_consulted"] = consulted_empty
    # Inventory aus → leerer Kontext, damit Pipeline weiterläuft
    if not is_agent_enabled("inventory_manager") and not overlay.get("stock_and_tool_context"):
        overlay["stock_and_tool_context"] = {
            "sender_agent": "Inventory_Manager",
            "recipient_agent": "3D_Builder",
            "payload_type": "CONTEXT_INJECTION",
            "data": {
                "recommended_tools": [],
                "stock_constraints": {},
                "relevant_rules": [],
                "relevant_assets": [],
                "skipped": True,
                "reason": "inventory_manager disabled",
            },
        }
    # V&V aus
    if not is_agent_enabled("vv_manager"):
        overlay["vv_consulted_phases"] = ["concept", "design", "manufacturing"]
        overlay["vv_needs_alignment"] = False
        if overlay.get("escalation_reason") in {"requirements_approval", "requirements_question", "requirements_confirm"}:
            overlay["human_approval_required"] = False
            overlay["escalation_reason"] = None
    # Human-Eskalation aus → Konzept auto-freigeben wenn nötig
    if not is_agent_enabled("human_escalation"):
        if overlay.get("escalation_reason") == "concept_approval" or (
            overlay.get("requirements_contract") and not overlay.get("concept_approved")
        ):
            overlay["concept_approved"] = True
            overlay["human_approval_required"] = False
            if overlay.get("escalation_reason") == "concept_approval":
                overlay["escalation_reason"] = None
        if overlay.get("escalation_reason") in {
            "requirements_approval",
            "requirements_question",
            "requirements_confirm",
        }:
            overlay["vv_needs_alignment"] = False
            overlay["human_approval_required"] = False
            overlay["escalation_reason"] = None
    # Fertigung aus
    if not is_agent_enabled("fertigung_specialist"):
        overlay["manufacturing_assessed"] = True
    # Montage aus
    if not is_agent_enabled("montage_manager"):
        overlay["montage_assessed"] = True
    # 3D / Validator aus
    if not is_agent_enabled("builder_3d") and not overlay.get("generated_code"):
        overlay["generated_code"] = "# skipped: builder_3d disabled"
        overlay["sandbox_result"] = {
            "status": "SUCCESS",
            "error_type": None,
            "error_message": None,
            "traceback": None,
            "stdout": "builder_3d disabled – skipped",
            "export_paths": [],
        }
    elif not is_agent_enabled("validator") and overlay.get("generated_code") and not overlay.get("sandbox_result"):
        overlay["sandbox_result"] = {
            "status": "SUCCESS",
            "error_type": None,
            "error_message": None,
            "traceback": None,
            "stdout": "validator disabled – skipped",
            "export_paths": [],
        }
    return overlay


def _compute_next_route(state: AgentState) -> str:
    """Gemeinsame Routing-Logik für Node-Preview und Conditional Edges."""
    effective = _overlay_for_disabled_agents(state)
    reason = effective.get("escalation_reason")
    if effective.get("human_approval_required") and reason in _USER_ESCALATION_REASONS:
        return "human_escalation" if is_agent_enabled("human_escalation") else _compute_next_route(
            {**effective, "human_approval_required": False, "escalation_reason": None, "concept_approved": True}
        )

    refinement = effective.get("refinement_request")
    if refinement:
        target = refinement.get("target") or "concept_builder"
        allowed = {
            "flexible_specialist",
            "custom_agent_1",
            "custom_agent_2",
            "vv_manager",
            "concept_builder",
            "inventory_manager",
            "fertigung_specialist",
            "montage_manager",
            "builder_3d",
            "validator",
            "human_escalation",
        }
        if target in allowed and is_agent_enabled(target):
            return target
        # Ziel deaktiviert → Refinement verwerfen und normal weiter
        effective = {**effective, "refinement_request": None}

    if effective.get("vv_needs_alignment") or (
        effective.get("human_approval_required") and reason == "requirements_approval"
    ):
        if is_agent_enabled("human_escalation"):
            return "human_escalation"
        effective = {
            **effective,
            "vv_needs_alignment": False,
            "human_approval_required": False,
            "escalation_reason": None,
        }

    # 1) Flexible Specialist (optional)
    if not effective.get("flexible_consulted") and is_agent_enabled("flexible_specialist"):
        return "flexible_specialist"

    # 1b) Leer-Agenten (Guidance-only, optional)
    empty_done = set(effective.get("empty_agents_consulted") or [])
    for aid in ("custom_agent_1", "custom_agent_2"):
        if is_agent_enabled(aid) and aid not in empty_done:
            return aid

    consulted = list(effective.get("vv_consulted_phases") or [])
    # 2) V&V concept
    if "concept" not in consulted:
        if is_agent_enabled("vv_manager"):
            return "vv_manager"
        consulted = list(dict.fromkeys([*consulted, "concept", "design", "manufacturing"]))

    # 3) Konzept
    if not effective.get("requirements_contract"):
        if is_agent_enabled("concept_builder"):
            return "concept_builder"
        return "END"

    if not effective.get("concept_approved"):
        if reason == "concept_approval" and is_agent_enabled("human_escalation"):
            return "human_escalation"
        if is_agent_enabled("concept_builder"):
            return "concept_builder"
        # ohne Concept/Human: freigeben und weiter
        effective = {**effective, "concept_approved": True}

    # 4) V&V design
    if "design" not in consulted:
        if is_agent_enabled("vv_manager"):
            return "vv_manager"
        consulted = list(dict.fromkeys([*consulted, "design"]))

    parts = (effective.get("requirements_contract") or {}).get("parts", [])
    if parts and effective.get("current_part_index", 0) >= len(parts):
        if "manufacturing" not in consulted and is_agent_enabled("vv_manager"):
            return "vv_manager"
        if not effective.get("montage_assessed") and is_agent_enabled("montage_manager"):
            return "montage_manager"
        return "END"

    # 5) Inventory (optional)
    if not effective.get("stock_and_tool_context"):
        if is_agent_enabled("inventory_manager"):
            return "inventory_manager"
        # Overlay sollte greifen; Fallback stub
        return "fertigung_specialist" if is_agent_enabled("fertigung_specialist") else (
            "builder_3d" if is_agent_enabled("builder_3d") else "END"
        )

    # 6) Fertigung
    if not effective.get("manufacturing_assessed"):
        if is_agent_enabled("fertigung_specialist"):
            return "fertigung_specialist"
        effective = {**effective, "manufacturing_assessed": True}

    # 7) V&V manufacturing
    if "manufacturing" not in consulted:
        if is_agent_enabled("vv_manager"):
            return "vv_manager"

    # 8) 3D + Validator
    if not effective.get("generated_code"):
        if is_agent_enabled("builder_3d"):
            return "builder_3d"
        return "END"

    sandbox_result = effective.get("sandbox_result")
    if not sandbox_result:
        if is_agent_enabled("validator"):
            return "validator"
        return "END"

    if sandbox_result.get("status") != "SUCCESS":
        if is_agent_enabled("builder_3d"):
            return "builder_3d"
        return "END"

    # Nächstes Teil / Abschluss über Supervisor-Advance
    if is_agent_enabled("inventory_manager"):
        return "inventory_manager"
    return "END"


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
