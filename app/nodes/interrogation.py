import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import OpenAIClient
from app.prompt import INTERROGATION_PROMPT
from app.state import State

logger = logging.getLogger(__name__)


class InterrogationDecision(BaseModel):
    action: Literal["ask_question", "done", "dropped", "blocked"]
    question: str = Field(default="")
    rationale: str = Field(default="")
    root_cause: str = Field(default="")


class InterrogationNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = INTERROGATION_PROMPT

    def run(self, state: State) -> State:
        todo_offset = state["todo_offset"]
        todo = state["todos"][todo_offset]
        logger.info(
            "Interrogation running for todo %s with %s messages",
            todo["id"],
            len(todo["interview_messages"]),
        )
        history_lines = []
        for msg in todo["interview_messages"]:
            history_lines.append(f'{msg["role"]}: {msg["content"]}')

        history_text = "\n".join(history_lines) if history_lines else "(no interview messages yet)"
        solved_todo_lines = []
        for idx, item in enumerate(state["todos"]):
            if idx == todo_offset:
                continue
            if item["status"] != "solved":
                continue
            solved_todo_lines.append(
                "\n".join(
                    [
                        f"ID: {item['id']}",
                        f"Title: {item['title']}",
                        f"Status: {item['status']}",
                        f"Resolution: {item.get('resolution', '')}",
                        f"Root cause: {item.get('root_cause', '').strip()}",
                        f"Evidence: {item.get('evidence', [])}",
                    ]
                )
            )
        solved_todo_context = (
            "\n\n".join(solved_todo_lines) if solved_todo_lines else "(no solved todo items yet)"
        )

        payload = (
            f"{self.prompt}\n\n"
            f"Stakeholder profile:\n{state['stakeholder']}\n\n"
            f"Todo title:\n{todo['title']}\n\n"
            f"Todo description:\n{todo['description']}\n\n"
            f"Solved todo context from other items:\n{solved_todo_context}\n\n"
            f"Interview history:\n{history_text}"
        )

        structured_llm = self.llm.with_structured_output(InterrogationDecision)
        decision: InterrogationDecision = structured_llm.invoke(payload)

        if decision.action in {"done", "dropped", "blocked"}:
            todo["status"] = "solved"
            todo["resolution"] = decision.action
            if decision.root_cause.strip():
                todo["root_cause"] = decision.root_cause.strip()
                todo["evidence"].append(f"Root cause: {todo['root_cause']}")
            if decision.rationale:
                todo["evidence"].append(decision.rationale)
            state["current_question"] = ""
            logger.info(
                "Interrogation ended todo %s with status=%s resolution=%s",
                todo["id"],
                todo["status"],
                decision.action,
            )
            return state

        question = decision.question.strip()
        if not question:
            todo["status"] = "solved"
            todo["resolution"] = "blocked"
            todo["root_cause"] = "Insufficient interview specificity: no valid follow-up question generated."
            todo["evidence"].append("Interrogation returned an empty follow-up question.")
            state["current_question"] = ""
            logger.warning("Interrogation produced empty question for todo %s", todo["id"])
            return state

        todo["status"] = "pending"
        todo["interview_messages"].append({"role": "assistant", "content": question})
        state["current_question"] = question
        logger.info("Interrogation asked question for todo %s", todo["id"])
        return state
