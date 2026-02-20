import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm import OpenAIClient
from app.prompt import DISTILLATION_PROMPT
from app.state import State


logger = logging.getLogger(__name__)


class DistilledTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Short todo title")
    description: str = Field(description="Detailed, testable validation todo item")

    @model_validator(mode="after")
    def _validate_text(self) -> "DistilledTodo":
        if not self.title.strip():
            raise ValueError("title must be non-empty")
        if not self.description.strip():
            raise ValueError("description must be non-empty")
        return self


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todos: list[DistilledTodo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_todos(self) -> "DistillationResult":
        if not self.todos:
            raise ValueError("todos must contain at least one item")
        return self


class DistillationNode:
    def __init__(self):
        self.llm = OpenAIClient().get_client()
        self.prompt = DISTILLATION_PROMPT

    def run(self, state: State):
        todo_items = state.get("todo_items", [])
        structured_todos = []
        if todo_items:
            logger.info("Preparing %s provided todo items for validation", len(todo_items))
            for index, item in enumerate(todo_items, start=1):
                title = item["title"].strip() or f"Todo {index}"
                description = item["description"].strip() or title
                structured_todos.append(
                    {
                        "id": f"t-{index}",
                        "title": title,
                        "description": description,
                        "status": "pending",
                        "resolution": "",
                        "root_cause": "",
                        "evidence": [],
                        "interview_messages": [],
                    }
                )
        else:
            logger.info("Distillation started: generating todos from idea input")
            payload = (
                f"{self.prompt}\n\n"
                f"User statement:\n{state['user_input']}\n\n"
                f"Stakeholder:\n{state['stakeholder']}"
            )
            structured_llm = self.llm.with_structured_output(DistillationResult)
            print("-" * 80)
            print("Printign distillation output")
            print(structured_llm)
            print("-" * 80)
            result: DistillationResult = structured_llm.invoke(payload)
            print("-" * 80)
            print("Printign result")
            print(result)
            print("-" * 80)
            for index, item in enumerate(result.todos, start=1):
                title = item.title.strip() or f"Todo {index}"
                description = item.description.strip() or title
                structured_todos.append(
                    {
                        "id": f"t-{index}",
                        "title": title,
                        "description": description,
                        "status": "pending",
                        "resolution": "",
                        "root_cause": "",
                        "evidence": [],
                        "interview_messages": [],
                    }
                )

        state["todos"] = structured_todos
        logger.info("-" * 80)
        logger.info("Prepared %s todos", len(structured_todos))
        logger.info("-" * 80)
        state["todo_offset"] = 0
        state["current_question"] = ""
        state["max_interview_messages"] = state.get("max_interview_messages", 12)
        logger.info("Prepared %s todos", len(structured_todos))
        return state
