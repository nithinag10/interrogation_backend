from typing import Literal, TypedDict


HypothesisStatus = Literal[
    "pending",
    "in_progress",
    "validated",
    "invalidated",
    "cannot_validate",
]


class InterviewMessage(TypedDict):
    role: Literal["assistant", "user"]
    content: str


class Hypothesis(TypedDict):
    id: str
    title: str
    description: str
    status: HypothesisStatus
    root_cause: str
    evidence: list[str]
    interview_messages: list[InterviewMessage]


class State(TypedDict):
    hypothesis: list[Hypothesis]
    user_input: str
    stakeholder: str
    hypothesis_offset: int
    final_answer: str
    max_interview_messages: int
    current_question: str
