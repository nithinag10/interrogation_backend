import logging

from app.state import State

TERMINAL_STATUSES = {"solved"}
logger = logging.getLogger(__name__)


class TodoManagerNode:
    """Select current todo for validation. No interview side-effects here."""

    def run(self, state: State) -> State:
        todos = state["todos"]
        offset = state["todo_offset"]

        while offset < len(todos) and todos[offset]["status"] in TERMINAL_STATUSES:
            offset += 1

        state["todo_offset"] = offset

        if offset >= len(todos):
            logger.info("Manager found all todos completed: count=%s", len(todos))
            return state

        todo = todos[offset]
        logger.info(
            "Manager selected todo %s at offset=%s status=%s",
            todo["id"],
            offset,
            todo["status"],
        )
        return state


HypothesisManagerNode = TodoManagerNode
