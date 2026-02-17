import logging

from app.llm import OpenAIClient
from app.prompt import STAKEHOLDER_PROFILE_PROMPT
from app.state import State

logger = logging.getLogger(__name__)


class StakeholderNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = STAKEHOLDER_PROFILE_PROMPT

    def run(self, state: State) -> State:
        hypothesis = state["hypothesis"][state["hypothesis_offset"]]
        question = state.get("current_question", "").strip()

        if not question:
            logger.info("Stakeholder skipped: no pending question")
            return state

        history_lines = []
        for msg in hypothesis["interview_messages"]:
            history_lines.append(f'{msg["role"]}: {msg["content"]}')
        history_text = "\n".join(history_lines) if history_lines else "(no interview messages yet)"

        payload = (
            f"{self.prompt}\n\n"
            f"Stakeholder profile:\n{state['stakeholder']}\n\n"
            f"Interview conversation so far:\n{history_text}\n\n"
            f"Interviewer question:\n{question}\n\n"
            f"Answer as the stakeholder:"
        )

        response = self.llm.invoke(payload)
        content = response.content if hasattr(response, "content") else str(response)

        hypothesis["interview_messages"].append(
            {"role": "user", "content": content}
        )

        state["current_question"] = ""
        logger.info("Stakeholder answered hypothesis %s", hypothesis["id"])
        return state
