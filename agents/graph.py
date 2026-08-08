"""LangGraph State Machine – Agenten-Topologie & Routing
(SPEC Kap. 3.1 Topologie, 3.4 Loop-Mechanik, 3.5 Eskalation)."""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.nodes.builder_3d import builder_3d_node
from agents.nodes.concept_builder import concept_builder_node
from agents.nodes.human_escalation import human_escalation_node
from agents.nodes.inventory_manager import inventory_manager_node
from agents.nodes.supervisor import route_from_supervisor, supervisor_node
from agents.nodes.validator import validator_node
from agents.state import AgentState

_SUPERVISOR_ROUTE_MAP = {
    "concept_builder": "concept_builder",
    "inventory_manager": "inventory_manager",
    "builder_3d": "builder_3d",
    "validator": "validator",
    "human_escalation": "human_escalation",
    "END": END,
}


def create_agent_graph():
    """Baut und kompiliert den LangGraph-Workflow (SPEC Kap. 3).

    Topologie (Kap. 3.1): Der Supervisor ist der zentrale Router; jeder
    Fach-Agent kehrt nach seiner Arbeit zum Supervisor zurück, der anhand des
    States über den nächsten Schritt, eine Korrekturschleife (Kap. 3.4) oder
    eine User-Eskalation (Kap. 3.5, `interrupt()`) entscheidet.

    Ein `MemorySaver`-Checkpointer ist erforderlich, damit `interrupt()`
    den Graphen pro `thread_id` (Session) pausieren und über
    `Command(resume=...)` fortsetzen kann.
    """
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("concept_builder", concept_builder_node)
    graph.add_node("inventory_manager", inventory_manager_node)
    graph.add_node("builder_3d", builder_3d_node)
    graph.add_node("validator", validator_node)
    graph.add_node("human_escalation", human_escalation_node)

    graph.add_edge(START, "supervisor")
    graph.add_edge("concept_builder", "supervisor")
    graph.add_edge("inventory_manager", "supervisor")
    graph.add_edge("builder_3d", "supervisor")
    graph.add_edge("validator", "supervisor")
    graph.add_edge("human_escalation", "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, _SUPERVISOR_ROUTE_MAP)

    return graph.compile(checkpointer=MemorySaver())


WORKFLOW = create_agent_graph()
