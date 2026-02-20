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
        todo = state["todos"][state["todo_offset"]]
        question = state.get("current_question", "").strip()

        if not question:
            logger.info("Stakeholder skipped: no pending question")
            return state

        history_lines = []
        for msg in todo["interview_messages"]:
            history_lines.append(f'{msg["role"]}: {msg["content"]}')
        history_text = "\n".join(history_lines) if history_lines else "(no interview messages yet)"

        system_prompt = (
            f"{self.prompt}\n\n"
            f"You are this stakeholder:\n{state['stakeholder']}\n\n"
            "Stay fully in character."
        )
        user_prompt = (
            f"Interview conversation so far:\n{history_text}\n\n"
            f"Interviewer question:\n{question}\n\n"
            "Answer as the stakeholder."
        )

        response = self.llm.invoke(
            [
                ("system", system_prompt),
                ("human", user_prompt),
            ]
        )
        content = response.content if hasattr(response, "content") else str(response)

        todo["interview_messages"].append(
            {"role": "user", "content": content}
        )

        state["current_question"] = ""
        logger.info("Stakeholder answered todo %s", todo["id"])
        return state
