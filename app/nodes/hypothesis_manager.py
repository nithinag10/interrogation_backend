import logging

from app.state import State

TERMINAL_STATUSES = {"validated", "invalidated", "cannot_validate"}
logger = logging.getLogger(__name__)


class HypothesisManagerNode:
    """Select current hypothesis for validation. No interview side-effects here."""

    def run(self, state: State) -> State:
        hypotheses = state["hypothesis"]
        offset = state["hypothesis_offset"]

        while offset < len(hypotheses) and hypotheses[offset]["status"] in TERMINAL_STATUSES:
            offset += 1

        state["hypothesis_offset"] = offset

        if offset >= len(hypotheses):
            logger.info("Manager found all hypotheses completed: count=%s", len(hypotheses))
            return state

        hypothesis = hypotheses[offset]
        if hypothesis["status"] == "pending":
            hypothesis["status"] = "in_progress"

        logger.info(
            "Manager selected hypothesis %s at offset=%s status=%s",
            hypothesis["id"],
            offset,
            hypothesis["status"],
        )
        return state
