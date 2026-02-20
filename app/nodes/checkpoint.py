import logging

from app.state import State


TERMINAL_STATUSES = {"solved"}
logger = logging.getLogger(__name__)


class CheckpointNode:
    """Checks todo termination conditions between graph steps."""

    def run(self, state: State) -> State:
        offset = state["todo_offset"]
        if offset >= len(state["todos"]):
            logger.info("Checkpoint: no active todo")
            return state

        todo = state["todos"][offset]
        max_messages = state.get("max_interview_messages", 3)

        if (
            todo["status"] not in TERMINAL_STATUSES
            and len(todo["interview_messages"]) >= max_messages
        ):
            todo["status"] = "solved"
            todo["resolution"] = "blocked"
            todo["root_cause"] = "Interview depth limit reached before obtaining sufficient evidence."
            todo["evidence"].append("Interview terminated: message limit reached.")
            logger.info(
                "Checkpoint terminated todo %s at message limit %s",
                todo["id"],
                max_messages,
            )
        else:
            logger.info(
                "Checkpoint pass todo %s status=%s messages=%s",
                todo["id"],
                todo["status"],
                len(todo["interview_messages"]),
            )

        return state
