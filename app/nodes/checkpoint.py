import logging

from app.state import State


TERMINAL_STATUSES = {"validated", "invalidated", "cannot_validate"}
logger = logging.getLogger(__name__)


class CheckpointNode:
    """Checks hypothesis termination conditions between graph steps."""

    def run(self, state: State) -> State:
        offset = state["hypothesis_offset"]
        if offset >= len(state["hypothesis"]):
            logger.info("Checkpoint: no active hypothesis")
            return state

        hypothesis = state["hypothesis"][offset]
        max_messages = state.get("max_interview_messages", 3)

        if (
            hypothesis["status"] not in TERMINAL_STATUSES
            and len(hypothesis["interview_messages"]) >= max_messages
        ):
            hypothesis["status"] = "cannot_validate"
            hypothesis["root_cause"] = "Interview depth limit reached before obtaining sufficient evidence."
            hypothesis["evidence"].append("Interview terminated: message limit reached.")
            logger.info(
                "Checkpoint terminated hypothesis %s at message limit %s",
                hypothesis["id"],
                max_messages,
            )
        else:
            logger.info(
                "Checkpoint pass hypothesis %s status=%s messages=%s",
                hypothesis["id"],
                hypothesis["status"],
                len(hypothesis["interview_messages"]),
            )

        return state
