"""Master / Supervisor Agent – Orchestrator, Router, Mehrteil-Fortschritt &amp;
Eskalations-Wächter (SPEC Kap. 3.2.1, 3.4, 3.5).

Nutzer-Feedback: eine Anfrage kann mehrere unabhängige Teile umfassen
(`requirements_contract.parts`). Der Supervisor erkennt den Abschluss eines
Teils (`sandbox_result.status == "SUCCESS"`), sichert das Ergebnis in
`completed_parts`, setzt die Scratch-Felder zurück und rückt zum nächsten
Teil vor, bevor die reguläre Iterations-/Eskalations-Prüfung greift."""

import logging

from agents.state import AgentState
from agents.transcript import make_entry
from app.services.agent_workflow_store import get_max_iterations

logger = logging.getLogger(__name__)


def _advance_to_next_part(state: AgentState) -> AgentState | None:
    """Schließt das aktuelle Teil ab (falls erfolgreich validiert) und rückt
    zum nächsten Teil der Teile-Liste vor. Gibt `None` zurück, falls es
    nichts zum Abschließen gibt (reguläre Iteration)."""
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
            }
        )

    logger.info("Supervisor: Teil %s/%s abgeschlossen.", idx + 1, len(parts))
    next_idx = idx + 1
    next_hint = (
        f"Nächstes Teil: {parts[next_idx].get('name')}" if next_idx < len(parts) else "Alle Teile fertig → END"
    )

    return {
        "completed_parts": completed,
        "current_part_index": next_idx,
        "stock_and_tool_context": None,
        "generated_code": None,
        "sandbox_result": None,
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
                to_agent="inventory_manager" if next_idx < len(parts) else "END",
            )
        ],
    }


def supervisor_node(state: AgentState) -> AgentState:
    """Zählt Iterationen, rückt bei Erfolg zum nächsten Teil vor.
    Überschrittenes Iterationslimit führt nicht mehr zur User-Eskalation –
    die Pipeline arbeitet weiter (nur Warnung im Transcript)."""

    advance_update = _advance_to_next_part(state)
    if advance_update is not None:
        return advance_update

    iteration_count = state.get("iteration_count", 0) + 1
    parts = (state.get("requirements_contract") or {}).get("parts", [])
    idx = state.get("current_part_index", 0)
    part_name = parts[idx].get("name") if 0 <= idx < len(parts) else None

    # Vorausschau der Route (gleiche Logik wie route_from_supervisor) fürs Log.
    if state.get("human_approval_required") and state.get("escalation_reason") == "concept_approval":
        next_route = "human_escalation"
    elif not state.get("requirements_contract"):
        next_route = "concept_builder"
    elif state.get("refinement_request"):
        next_route = (state.get("refinement_request") or {}).get("target", "concept_builder")
    elif not state.get("concept_approved"):
        next_route = (
            "human_escalation"
            if state.get("escalation_reason") == "concept_approval"
            else "concept_builder"
        )
    elif idx >= len(parts) and parts:
        next_route = "END"
    elif not state.get("stock_and_tool_context"):
        next_route = "inventory_manager"
    elif not state.get("generated_code"):
        next_route = "builder_3d"
    elif not state.get("sandbox_result"):
        next_route = "validator"
    elif (state.get("sandbox_result") or {}).get("status") != "SUCCESS":
        next_route = "builder_3d"
    else:
        next_route = "inventory_manager"

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
                    "has_contract": bool(state.get("requirements_contract")),
                    "has_stock_context": bool(state.get("stock_and_tool_context")),
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
        # Keine User-Eskalation mehr: Agenten arbeiten weiter (Warnung nur im Log).
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

    # Veraltete Generic-Eskalations-Flags verwerfen (nur Konzept-Freigabe bleibt)
    if state.get("human_approval_required") and state.get("escalation_reason") != "concept_approval":
        updates["human_approval_required"] = False
        updates["escalation_reason"] = None

    # Fehlgeschlagene Sandbox → Code/Result löschen, damit Builder neu generiert
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
    """Zentrale Routing-Logik. User-Interrupt nur noch für Konzept-Freigabe;
    alle anderen Engpässe werden intern weitergeschleift."""

    # Nur echte Konzept-Freigabe pausiert für den Nutzer
    if state.get("human_approval_required") and state.get("escalation_reason") == "concept_approval":
        return "human_escalation"

    if not state.get("requirements_contract"):
        return "concept_builder"

    refinement = state.get("refinement_request")
    if refinement:
        return refinement.get("target", "concept_builder")

    if not state.get("concept_approved"):
        # Noch kein Freigabe-Flag: zurück zum Concept Builder (setzt Freigabe-Gate)
        if state.get("escalation_reason") == "concept_approval":
            return "human_escalation"
        return "concept_builder"

    parts = (state.get("requirements_contract") or {}).get("parts", [])
    if state.get("current_part_index", 0) >= len(parts):
        return "END"

    if not state.get("stock_and_tool_context"):
        return "inventory_manager"

    if not state.get("generated_code"):
        return "builder_3d"

    sandbox_result = state.get("sandbox_result")
    if not sandbox_result:
        return "validator"

    # Stuck-State (z.B. fehlgeschlagene Sandbox ohne Refinement): neu versuchen
    if sandbox_result.get("status") != "SUCCESS":
        return "builder_3d"

    # SUCCESS hätte von _advance_to_next_part verarbeitet werden sollen –
    # Sicherheitsnetz: zum nächsten Schritt / Inventory
    return "inventory_manager"
