import logging

from app.llm import OpenAIClient
from app.prompt import BUSINESS_EXPERT_PROMPT
from app.state import State

logger = logging.getLogger(__name__)


class BusinessExpertNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = BUSINESS_EXPERT_PROMPT

    def run(self, state: State) -> State:
        hypothesis_sections = []
        for hyp in state["hypothesis"]:
            transcript_lines = []
            for message in hyp["interview_messages"]:
                transcript_lines.append(f"{message['role']}: {message['content']}")
            transcript = "\n".join(transcript_lines) if transcript_lines else "(no interview transcript)"

            hypothesis_sections.append(
                "\n".join(
                    [
                        f"Hypothesis ID: {hyp['id']}",
                        f"Title: {hyp['title']}",
                        f"Description: {hyp['description']}",
                        f"Status: {hyp['status']}",
                        f"Root cause: {hyp.get('root_cause', '')}",
                        f"Evidence: {hyp['evidence']}",
                        "Interview transcript:",
                        transcript,
                    ]
                )
            )

        payload = (
            f"{self.prompt}\n\n"
            f"User statement:\n{state['user_input']}\n\n"
            f"Stakeholder profile:\n{state['stakeholder']}\n\n"
            f"Hypothesis results and transcripts:\n\n{chr(10).join(hypothesis_sections)}"
        )

        response = self.llm.invoke(payload)
        logger.info("-" * 80)
        logger.info("Business expert response: %s", response)
        logger.info("-" * 80)
        state["final_answer"] = response.content if hasattr(response, "content") else str(response)
        logger.info("Business expert generated final answer")
        return state
