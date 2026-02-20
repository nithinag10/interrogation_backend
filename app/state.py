from typing import Literal, TypedDict


TodoStatus = Literal[
    "pending",
    "solved",
]


class InterviewMessage(TypedDict):
    role: Literal["assistant", "user"]
    content: str


class TodoItem(TypedDict):
    id: str
    title: str
    description: str
    status: TodoStatus
    resolution: Literal["", "done", "dropped", "blocked"]
    root_cause: str
    evidence: list[str]
    interview_messages: list[InterviewMessage]


class State(TypedDict):
    todos: list[TodoItem]
    todo_items: list[dict[str, str]]
    user_input: str
    stakeholder: str
    todo_offset: int
    final_answer: str
    max_interview_messages: int
    current_question: str
