"""Human Escalation Node – nur noch für Konzept-Freigabe (interrupt).

Andere frühere Eskalationsgründe (Iterationslimit, Inventar-Lücken, wiederholte
Sandbox-Fehler) werden ohne User-Pause fortgesetzt – die Agenten arbeiten weiter.
"""

import logging

from langgraph.types import interrupt

from agents.state import AgentState
from agents.transcript import make_entry, truncate

logger = logging.getLogger(__name__)


def _handle_concept_approval(state: AgentState) -> AgentState:
    payload = {
        "reason": "concept_approval",
        "requirements_contract": state.get("requirements_contract"),
        "concept_sketch_svg": state.get("concept_sketch_svg"),
        "concept_image_url": state.get("concept_image_url"),
        "iteration_count": state.get("iteration_count", 0),
    }

    logger.info("Human Escalation: Entwurf pausiert zur Freigabe.")
    decision = interrupt(payload)
    logger.info("Human Escalation: Entwurfs-Entscheidung: %s", decision)

    if isinstance(decision, dict) and decision.get("decision") == "revise":
        feedback = decision.get("feedback") or ""
        return {
            "human_approval_required": False,
            "escalation_reason": None,
            "iteration_count": 0,
            "current_agent": "human_escalation",
            "refinement_request": {
                "from_agent": "human",
                "target": "concept_builder",
                "reason": "concept_feedback",
                "feedback": feedback,
            },
            "messages": [
                {"role": "system", "content": "[Human Escalation] Nutzer fordert Überarbeitung des Entwurfs an."}
            ],
            "agent_transcript": [
                make_entry(
                    "human_escalation",
                    "concept_revise",
                    "Nutzer: Entwurf überarbeiten",
                    detail={"feedback": truncate(feedback, 800)},
                    to_agent="concept_builder",
                )
            ],
        }

    return {
        "human_approval_required": False,
        "escalation_reason": None,
        "iteration_count": 0,
        "concept_approved": True,
        "current_agent": "human_escalation",
        "messages": [{"role": "system", "content": "[Human Escalation] Entwurf freigegeben – Ausarbeitung startet."}],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "concept_approved",
                "Nutzer: Entwurf freigegeben → Ausarbeitung",
                detail={"decision": decision},
                to_agent="inventory_manager",
            )
        ],
    }


def _auto_continue(state: AgentState) -> AgentState:
    """Frühere Generic-Eskalationen: Flags löschen und Pipeline fortsetzen."""
    reason = state.get("escalation_reason") or "unbekannt"
    logger.warning(
        "Human Escalation übersprungen (kein User-Interrupt): %s – Agenten arbeiten weiter.",
        reason,
    )
    updates: AgentState = {
        "human_approval_required": False,
        "escalation_reason": None,
        "current_agent": "human_escalation",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"[Human Escalation] Keine User-Pause ({reason}). "
                    "Pipeline setzt die Arbeit fort."
                ),
            }
        ],
        "agent_transcript": [
            make_entry(
                "human_escalation",
                "auto_continue",
                f"Eskalation '{reason}' übersprungen – weiter ohne User",
                detail={"reason": reason, "iteration_count": state.get("iteration_count", 0)},
                to_agent="supervisor",
            )
        ],
    }

    sandbox_result = state.get("sandbox_result")
    if sandbox_result and sandbox_result.get("status") != "SUCCESS":
        updates["generated_code"] = None
        updates["sandbox_result"] = None
        # Leichter Retry-Hinweis an den Builder
        updates["refinement_request"] = {
            "from_agent": "human_escalation",
            "target": "builder_3d",
            "reason": f"auto_continue_after:{reason}",
        }
    else:
        stock_context = state.get("stock_and_tool_context") or {}
        recommended_tools = stock_context.get("data", {}).get("recommended_tools")
        if not recommended_tools:
            updates["stock_and_tool_context"] = None

    return updates


def human_escalation_node(state: AgentState) -> AgentState:
    """Pausiert nur bei Konzept-Freigabe; alle anderen Gründe → Auto-Continue."""

    if state.get("escalation_reason") == "concept_approval":
        return _handle_concept_approval(state)

    return _auto_continue(state)
