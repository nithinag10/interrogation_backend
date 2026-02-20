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
        todo_sections = []
        for todo in state["todos"]:
            transcript_lines = []
            for message in todo["interview_messages"]:
                transcript_lines.append(f"{message['role']}: {message['content']}")
            transcript = "\n".join(transcript_lines) if transcript_lines else "(no interview transcript)"

            todo_sections.append(
                "\n".join(
                    [
                        f"Todo ID: {todo['id']}",
                        f"Title: {todo['title']}",
                        f"Description: {todo['description']}",
                        f"Status: {todo['status']}",
                        f"Resolution: {todo.get('resolution', '')}",
                        f"Root cause: {todo.get('root_cause', '')}",
                        f"Evidence: {todo['evidence']}",
                        "Interview transcript:",
                        transcript,
                    ]
                )
            )

        payload = (
            f"{self.prompt}\n\n"
            f"User statement:\n{state['user_input']}\n\n"
            f"Stakeholder profile:\n{state['stakeholder']}\n\n"
            f"Todo results and transcripts:\n\n{chr(10).join(todo_sections)}"
        )

        response = self.llm.invoke(payload)
        logger.info("-" * 80)
        logger.info("Business expert response: %s", response)
        logger.info("-" * 80)
        state["final_answer"] = response.content if hasattr(response, "content") else str(response)
        logger.info("Business expert generated final answer")
        return state
