import logging

from app.llm import OpenAIClient
from app.prompt import DISTILLATION_PROMPT
from app.state import State

from pydantic import BaseModel, Field


class DistilledHypothesis(BaseModel):
    title: str = Field(description="Short hypothesis title")
    description: str = Field(description="Detailed, testable hypothesis statement")


class DistillationResult(BaseModel):
    hypotheses: list[DistilledHypothesis] = Field(default_factory=list)


logger = logging.getLogger(__name__)


class DistillationNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = DISTILLATION_PROMPT

    def run(self, state: State):
        user_statement = state["user_input"]
        stakeholder = state["stakeholder"]
        logger.info("Distillation started")

        payload = (
            f"{self.prompt}\n\n"
            f"User statement:\n{user_statement}\n\n"
            f"Stakeholder:\n{stakeholder}"
        )

        structured_llm = self.llm.with_structured_output(DistillationResult)
        result: DistillationResult = structured_llm.invoke(payload)

        structured_hypotheses = []
        for index, item in enumerate(result.hypotheses, start=1):
            structured_hypotheses.append(
                {
                    "id": f"h-{index}",
                    "title": item.title.strip() or f"Hypothesis {index}",
                    "description": item.description.strip(),
                    "status": "pending",
                    "root_cause": "",
                    "evidence": [],
                    "interview_messages": [],
                }
            )

        state["hypothesis"] = structured_hypotheses
        logger.info("-" * 80)
        logger.info("Distillation created %s hypotheses", len(structured_hypotheses))
        logger.info("-" * 80)
        state["hypothesis_offset"] = 0
        state["current_question"] = ""
        state["max_interview_messages"] = state.get("max_interview_messages", 12)
        logger.info("Distillation created %s hypotheses", len(structured_hypotheses))
        return state
