"""LangGraph State Machine – erweiterte Agenten-Topologie."""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.nodes.builder_3d import builder_3d_node
from agents.nodes.concept_builder import concept_builder_node
from agents.nodes.concept_critic import concept_critic_node
from agents.nodes.concept_panel_reviewer import concept_panel_reviewer_node
from agents.nodes.empty_agent import custom_agent_1_node, custom_agent_2_node
from agents.nodes.fertigung_specialist import fertigung_specialist_node
from agents.nodes.flexible_specialist import flexible_specialist_node
from agents.nodes.human_escalation import human_escalation_node
from agents.nodes.interior_architect import interior_architect_node
from agents.nodes.inventory_manager import inventory_manager_node
from agents.nodes.montage_manager import montage_manager_node
from agents.nodes.supervisor import route_from_supervisor, supervisor_node
from agents.nodes.validator import validator_node
from agents.nodes.vv_manager import vv_manager_node
from agents.state import AgentState

_SUPERVISOR_ROUTE_MAP = {
    "flexible_specialist": "flexible_specialist",
    "interior_architect": "interior_architect",
    "custom_agent_1": "custom_agent_1",
    "custom_agent_2": "custom_agent_2",
    "vv_manager": "vv_manager",
    "concept_builder": "concept_builder",
    "concept_critic": "concept_critic",
    "concept_panel_reviewer": "concept_panel_reviewer",
    "inventory_manager": "inventory_manager",
    "fertigung_specialist": "fertigung_specialist",
    "montage_manager": "montage_manager",
    "builder_3d": "builder_3d",
    "validator": "validator",
    "human_escalation": "human_escalation",
    "END": END,
}


def create_agent_graph():
    """Hub-and-Spoke inkl. Innenarchitekt, Konzept-Kritik und optionaler Leer-Agenten."""
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("flexible_specialist", flexible_specialist_node)
    graph.add_node("interior_architect", interior_architect_node)
    graph.add_node("custom_agent_1", custom_agent_1_node)
    graph.add_node("custom_agent_2", custom_agent_2_node)
    graph.add_node("vv_manager", vv_manager_node)
    graph.add_node("concept_builder", concept_builder_node)
    graph.add_node("concept_critic", concept_critic_node)
    graph.add_node("concept_panel_reviewer", concept_panel_reviewer_node)
    graph.add_node("inventory_manager", inventory_manager_node)
    graph.add_node("fertigung_specialist", fertigung_specialist_node)
    graph.add_node("montage_manager", montage_manager_node)
    graph.add_node("builder_3d", builder_3d_node)
    graph.add_node("validator", validator_node)
    graph.add_node("human_escalation", human_escalation_node)

    graph.add_edge(START, "supervisor")
    graph.add_edge("flexible_specialist", "supervisor")
    graph.add_edge("interior_architect", "supervisor")
    graph.add_edge("custom_agent_1", "supervisor")
    graph.add_edge("custom_agent_2", "supervisor")
    graph.add_edge("vv_manager", "supervisor")
    graph.add_edge("concept_builder", "supervisor")
    graph.add_edge("concept_critic", "supervisor")
    graph.add_edge("concept_panel_reviewer", "supervisor")
    graph.add_edge("inventory_manager", "supervisor")
    graph.add_edge("fertigung_specialist", "supervisor")
    graph.add_edge("montage_manager", "supervisor")
    graph.add_edge("builder_3d", "supervisor")
    graph.add_edge("validator", "supervisor")
    graph.add_edge("human_escalation", "supervisor")

    graph.add_conditional_edges("supervisor", route_from_supervisor, _SUPERVISOR_ROUTE_MAP)

    return graph.compile(checkpointer=MemorySaver())


WORKFLOW = create_agent_graph()
