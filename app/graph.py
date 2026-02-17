import logging

from langgraph.graph import END, START, StateGraph

from app.nodes.business_expert import BusinessExpertNode
from app.nodes.checkpoint import CheckpointNode
from app.nodes.distillation import DistillationNode
from app.nodes.hypothesis_manager import HypothesisManagerNode
from app.nodes.interrogation import InterrogationNode
from app.nodes.stakeholder import StakeholderNode
from app.state import State


TERMINAL_STATUSES = {"validated", "invalidated", "cannot_validate"}
logger = logging.getLogger(__name__)


def _route_after_manager(state: State) -> str:
    if state["hypothesis_offset"] >= len(state["hypothesis"]):
        logger.info("Route(manager): business_expert")
        return "business_expert"
    logger.info("Route(manager): interrogate hypothesis offset=%s", state["hypothesis_offset"])
    return "interrogate"


def _route_after_checkpoint(state: State) -> str:
    offset = state["hypothesis_offset"]
    if offset >= len(state["hypothesis"]):
        logger.info("Route(checkpoint): manager (offset out of range)")
        return "manager"

    current = state["hypothesis"][offset]
    if current["status"] in TERMINAL_STATUSES:
        logger.info("Route(checkpoint): manager (terminal status=%s)", current["status"])
        return "manager"

    if state.get("current_question", "").strip():
        logger.info("Route(checkpoint): stakeholder")
        return "stakeholder"

    logger.info("Route(checkpoint): interrogate")
    return "interrogate"


def build_graph(
    distillation_node: DistillationNode | None = None,
    manager_node: HypothesisManagerNode | None = None,
    interrogation_node: InterrogationNode | None = None,
    stakeholder_node: StakeholderNode | None = None,
    checkpoint_node: CheckpointNode | None = None,
    business_expert_node: BusinessExpertNode | None = None,
):
    graph = StateGraph(State)

    distillation = distillation_node or DistillationNode()
    manager = manager_node or HypothesisManagerNode()
    interrogation = interrogation_node or InterrogationNode()
    stakeholder = stakeholder_node or StakeholderNode()
    checkpoint = checkpoint_node or CheckpointNode()
    business_expert = business_expert_node or BusinessExpertNode()

    graph.add_node("distillation", distillation.run)
    graph.add_node("manager", manager.run)
    graph.add_node("interrogation", interrogation.run)
    graph.add_node("stakeholder", stakeholder.run)
    graph.add_node("checkpoint", checkpoint.run)
    graph.add_node("business_expert", business_expert.run)

    graph.add_edge(START, "distillation")
    graph.add_edge("distillation", "manager")
    graph.add_conditional_edges(
        "manager",
        _route_after_manager,
        {
            "interrogate": "interrogation",
            "business_expert": "business_expert",
        },
    )
    graph.add_edge("interrogation", "checkpoint")
    graph.add_edge("stakeholder", "checkpoint")
    graph.add_conditional_edges(
        "checkpoint",
        _route_after_checkpoint,
        {
            "manager": "manager",
            "stakeholder": "stakeholder",
            "interrogate": "interrogation",
        },
    )
    graph.add_edge("business_expert", END)

    return graph.compile()
