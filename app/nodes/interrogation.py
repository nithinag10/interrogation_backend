import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.llm import OpenAIClient
from app.prompt import INTERROGATION_PROMPT
from app.state import State

logger = logging.getLogger(__name__)


class InterrogationDecision(BaseModel):
    action: Literal["ask_question", "validated", "invalidated", "cannot_validate"]
    question: str = Field(default="")
    rationale: str = Field(default="")
    root_cause: str = Field(default="")


class InterrogationNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = INTERROGATION_PROMPT

    def run(self, state: State) -> State:
        hypothesis = state["hypothesis"][state["hypothesis_offset"]]
        logger.info(
            "Interrogation running for hypothesis %s with %s messages",
            hypothesis["id"],
            len(hypothesis["interview_messages"]),
        )
        history_lines = []
        for msg in hypothesis["interview_messages"]:
            history_lines.append(f'{msg["role"]}: {msg["content"]}')

        history_text = "\n".join(history_lines) if history_lines else "(no interview messages yet)"
        payload = (
            f"{self.prompt}\n\n"
            f"Stakeholder profile:\n{state['stakeholder']}\n\n"
            f"Hypothesis title:\n{hypothesis['title']}\n\n"
            f"Hypothesis description:\n{hypothesis['description']}\n\n"
            f"Interview history:\n{history_text}"
        )

        structured_llm = self.llm.with_structured_output(InterrogationDecision)
        decision: InterrogationDecision = structured_llm.invoke(payload)

        if decision.action in {"validated", "invalidated", "cannot_validate"}:
            hypothesis["status"] = decision.action
            if decision.root_cause.strip():
                hypothesis["root_cause"] = decision.root_cause.strip()
                hypothesis["evidence"].append(f"Root cause: {hypothesis['root_cause']}")
            if decision.rationale:
                hypothesis["evidence"].append(decision.rationale)
            state["current_question"] = ""
            logger.info(
                "Interrogation ended hypothesis %s with status=%s",
                hypothesis["id"],
                decision.action,
            )
            return state

        question = decision.question.strip()
        if not question:
            hypothesis["status"] = "cannot_validate"
            hypothesis["root_cause"] = "Insufficient interview specificity: no valid follow-up question generated."
            hypothesis["evidence"].append("Interrogation returned an empty follow-up question.")
            state["current_question"] = ""
            logger.warning("Interrogation produced empty question for hypothesis %s", hypothesis["id"])
            return state

        hypothesis["status"] = "in_progress"
        hypothesis["interview_messages"].append({"role": "assistant", "content": question})
        state["current_question"] = question
        logger.info("Interrogation asked question for hypothesis %s", hypothesis["id"])
        return state
