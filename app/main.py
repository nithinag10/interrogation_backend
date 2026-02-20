import asyncio
import json
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.graph import build_graph


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAKEHOLDER_FILE = PROJECT_ROOT / "data" / "stakeholders.json"
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


class StakeholderDefinition(BaseModel):
    id: str
    title: str
    profile: str
    age_demography: str
    tech_savviness: str
    product_context: str


class StakeholderCatalogResponse(BaseModel):
    stakeholders: list[StakeholderDefinition]


class StartSimulationRequest(BaseModel):
    idea: str | None = Field(default=None, min_length=1)
    user_input: str | None = Field(default=None, min_length=1)
    todo_list: list[str] | None = None
    stakeholder_id: str | None = None
    customer_persona: str | None = None
    stakeholder_profile: str | None = None
    max_interview_messages: int = Field(default=8, ge=2, le=40)


class StartSimulationResponse(BaseModel):
    simulation_id: str
    status: str
    events_url: str
    details_url: str


class SimulationStatusResponse(BaseModel):
    simulation_id: str
    status: str
    started_at: float
    completed_at: float | None = None
    error: str | None = None
    final_answer: str | None = None


class SimulationRuntime:
    def __init__(self, simulation_id: str):
        self.simulation_id = simulation_id
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.status = "running"
        self.started_at = time.time()
        self.completed_at: float | None = None
        self.error: str | None = None
        self.final_state: dict[str, Any] | None = None


def _cors_allow_origins() -> list[str]:
    configured = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return [origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()]
    return [
        "https://idea-sharpen.vercel.app",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


app = FastAPI(
    title="Interrogation Agent API",
    version="0.1.0",
    description="APIs to manage stakeholder definitions and stream live interview simulations.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
SIMULATIONS: dict[str, SimulationRuntime] = {}
SIMULATIONS_LOCK = threading.Lock()


def _default_state(
    max_interview_messages: int,
    user_input: str,
    stakeholder: str,
    todo_items: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "todos": [],
        "todo_items": todo_items,
        "user_input": user_input,
        "stakeholder": stakeholder,
        "todo_offset": 0,
        "final_answer": "",
        "max_interview_messages": max_interview_messages,
        "current_question": "",
    }


def _load_stakeholders() -> list[StakeholderDefinition]:
    if not STAKEHOLDER_FILE.exists():
        raise HTTPException(status_code=500, detail=f"Missing stakeholder file: {STAKEHOLDER_FILE}")

    with STAKEHOLDER_FILE.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise HTTPException(status_code=500, detail="Stakeholder file must contain a JSON array.")

    return [StakeholderDefinition.model_validate(item) for item in payload]


def _resolve_user_input(idea: str | None, user_input: str | None) -> str:
    if idea and idea.strip():
        return idea.strip()
    if user_input and user_input.strip():
        return user_input.strip()
    raise HTTPException(status_code=400, detail="Provide either idea or user_input.")


def _resolve_todo_items(todo_list: list[str] | None) -> list[dict[str, str]]:
    if todo_list is None:
        return []
    todo_items: list[dict[str, str]] = []
    for raw in todo_list:
        text = str(raw).strip()
        if not text:
            continue
        todo_items.append({"title": text, "description": text})

    if not todo_items:
        raise HTTPException(
            status_code=400,
            detail="todo_list was provided but no non-empty todo items were found.",
        )
    return todo_items


def _resolve_stakeholder_profile(
    stakeholder_id: str | None,
    stakeholder_profile: str | None,
    customer_persona: str | None,
) -> str:
    if customer_persona and customer_persona.strip():
        return customer_persona.strip()

    if stakeholder_profile and stakeholder_profile.strip():
        return stakeholder_profile.strip()

    if not stakeholder_id:
        raise HTTPException(
            status_code=400,
            detail="Provide either customer_persona/stakeholder_profile or stakeholder_id.",
        )

    stakeholders = _load_stakeholders()
    for stakeholder in stakeholders:
        if stakeholder.id == stakeholder_id:
            return stakeholder.profile.strip()

    raise HTTPException(status_code=404, detail=f"Stakeholder '{stakeholder_id}' was not found.")


def _emit(runtime: SimulationRuntime, event_type: str, payload: dict[str, Any]) -> None:
    runtime.queue.put(
        {
            "event": event_type,
            "timestamp": time.time(),
            "simulation_id": runtime.simulation_id,
            "payload": payload,
        }
    )


def _state_summary(state: dict[str, Any]) -> dict[str, Any]:
    todos = state.get("todos", [])
    return {
        "todo_offset": state.get("todo_offset"),
        "current_question": state.get("current_question"),
        "final_answer": state.get("final_answer"),
        "todos": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "status": item.get("status"),
                "resolution": item.get("resolution", ""),
                "root_cause": item.get("root_cause"),
                "evidence_count": len(item.get("evidence", [])),
                "interview_message_count": len(item.get("interview_messages", [])),
            }
            for item in todos
        ],
    }


def _normalize_todo_status(status: Any) -> str:
    value = str(status or "")
    if value == "solved":
        return "solved"
    return "pending"


def _emit_reasoning(
    runtime: SimulationRuntime,
    step: int,
    phase: str,
    message: str,
    todo_id: str | None = None,
    todo_title: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "step": step,
        "phase": phase,
        "message": message,
    }
    if todo_id:
        payload["todo_id"] = todo_id
    if todo_title:
        payload["todo_title"] = todo_title
    _emit(runtime, "reasoning.step", payload)


def _emit_agent_update(
    runtime: SimulationRuntime,
    step: int,
    stage: str,
    action: str,
    summary: str,
    details: dict[str, Any] | None = None,
    todo_id: str | None = None,
    todo_title: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "step": step,
        "stage": stage,
        "action": action,
        "summary": summary,
        "details": details or {},
    }
    if todo_id:
        payload["todo_id"] = todo_id
    if todo_title:
        payload["todo_title"] = todo_title
    _emit(runtime, "agent.update", payload)


def _transcript_payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    transcript_messages = []
    transcript_lines: list[str] = []
    for item in messages:
        role = str(item.get("role", ""))
        content = str(item.get("content", ""))
        transcript_messages.append({"role": role, "content": content})
        transcript_lines.append(f"{role}: {content}")
    return {
        "messages": transcript_messages,
        "text": "\n".join(transcript_lines),
        "message_count": len(transcript_messages),
    }


def _run_simulation(
    runtime: SimulationRuntime,
    request: StartSimulationRequest,
    user_input: str,
    stakeholder_profile: str,
    todo_items: list[dict[str, str]],
) -> None:
    try:
        graph = build_graph()
        state = _default_state(
            max_interview_messages=request.max_interview_messages,
            user_input=user_input,
            stakeholder=stakeholder_profile,
            todo_items=todo_items,
        )
        _emit(
            runtime,
            "simulation.started",
            {
                "max_interview_messages": request.max_interview_messages,
                "user_input": user_input,
                "todo_count": len(todo_items),
            },
        )
        _emit_reasoning(
            runtime,
            step=0,
            phase="thinking",
            message="Simulation started. Loading todo items and preparing validation flow.",
        )
        _emit_agent_update(
            runtime,
            step=0,
            stage="analysis",
            action="started",
            summary="Loading provided todo items for validation.",
            details={"user_input": user_input},
        )

        step = 0
        known_todos: set[str] = set()
        previous_status: dict[str, str] = {}
        previous_counts: dict[str, int] = {}
        previous_question: str = ""
        previous_offset: int = -1
        final_state = state
        for snapshot in graph.stream(state, stream_mode="values"):
            step += 1
            final_state = snapshot
            _emit(runtime, "simulation.step", {"step": step, "state": _state_summary(snapshot)})

            todos = snapshot.get("todos", [])

            if todos and not known_todos:
                using_todo_list = bool(snapshot.get("todo_items"))
                created_message = (
                    f"Loaded {len(todos)} todo items for validation."
                    if using_todo_list
                    else f"Generated {len(todos)} todos."
                )
                _emit_reasoning(
                    runtime,
                    step=step,
                    phase="thinking",
                    message=created_message,
                )
                _emit_agent_update(
                    runtime,
                    step=step,
                    stage="analysis",
                    action="completed",
                    summary=created_message,
                )
                _emit(
                    runtime,
                    "todo.batch_created",
                    {
                        "step": step,
                        "count": len(todos),
                        "todos": [
                            {
                                "todo_id": str(h.get("id", "")).strip(),
                                "todo_title": str(h.get("title", "")).strip(),
                                "todo_description": str(h.get("description", "")).strip(),
                            }
                            for h in todos
                        ],
                    },
                )
                if using_todo_list:
                    _emit(
                        runtime,
                        "todo.batch_loaded",
                        {
                            "step": step,
                            "count": len(todos),
                            "todo_items": [
                                {
                                    "todo_id": str(h.get("id", "")).strip(),
                                    "todo_title": str(h.get("title", "")).strip(),
                                    "todo_description": str(h.get("description", "")).strip(),
                                }
                                for h in todos
                            ],
                        },
                    )
                _emit_agent_update(
                    runtime,
                    step=step,
                    stage="validation_items",
                    action="created",
                    summary="Validation items prepared.",
                    details={"count": len(todos)},
                )

            for todo in todos:
                todo_id = str(todo.get("id", "")).strip()
                if not todo_id:
                    continue
                todo_title = str(todo.get("title", "")).strip() or todo_id
                status = _normalize_todo_status(todo.get("status"))

                if todo_id not in known_todos:
                    known_todos.add(todo_id)
                    _emit(
                        runtime,
                        "todo.created",
                        {
                            "step": step,
                            "todo_id": todo_id,
                            "todo_title": todo_title,
                            "todo_description": todo.get("description", ""),
                            "status": status,
                        },
                    )
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="todo_created",
                        message=f"Created {todo_id}: {todo_title}",
                        todo_id=todo_id,
                        todo_title=todo_title,
                    )
                    _emit_agent_update(
                        runtime,
                        step=step,
                        stage="todo_generation",
                        action="todo_created",
                        summary=f"Created {todo_id}: {todo_title}",
                        details={"status": status},
                        todo_id=todo_id,
                        todo_title=todo_title,
                    )

                prev_status = previous_status.get(todo_id)
                if prev_status != status:
                    _emit(
                        runtime,
                        "todo.validation",
                        {
                            "step": step,
                            "todo_id": todo_id,
                            "todo_title": todo_title,
                            "status": status,
                            "resolution": str(todo.get("resolution", "")),
                            "root_cause": todo.get("root_cause", ""),
                            "evidence_count": len(todo.get("evidence", [])),
                        },
                    )
                    resolution = str(todo.get("resolution", ""))
                    if status == "solved" and resolution == "done":
                        message = f"{todo_id} solved: done with sufficient interview evidence."
                    elif status == "solved" and resolution == "dropped":
                        message = f"{todo_id} solved: dropped based on interview evidence."
                    elif status == "solved" and resolution == "blocked":
                        message = f"{todo_id} solved: blocked due to insufficient or low-quality evidence."
                    elif status == "solved":
                        message = f"{todo_id} solved."
                    else:
                        message = f"Validating {todo_id}: gathering customer evidence."
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="todo_validation",
                        message=message,
                        todo_id=todo_id,
                        todo_title=todo_title,
                    )
                    _emit_agent_update(
                        runtime,
                        step=step,
                        stage="validation",
                        action="status_changed",
                        summary=message,
                        details={
                            "from_status": prev_status or "unknown",
                            "to_status": status,
                            "resolution": str(todo.get("resolution", "")),
                            "root_cause": str(todo.get("root_cause", "")),
                            "evidence": [str(item) for item in todo.get("evidence", [])],
                            "evidence_count": len(todo.get("evidence", [])),
                        },
                        todo_id=todo_id,
                        todo_title=todo_title,
                    )
                previous_status[todo_id] = status

            offset = int(snapshot.get("todo_offset", -1))
            if offset != previous_offset:
                previous_offset = offset
                if 0 <= offset < len(todos):
                    active = todos[offset]
                    active_id = str(active.get("id", "")).strip()
                    active_title = str(active.get("title", "")).strip() or active_id
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="thinking",
                        message=f"Focused validation on {active_id}: {active_title}.",
                        todo_id=active_id,
                        todo_title=active_title,
                    )
                    _emit(
                        runtime,
                        "todo.focused",
                        {
                            "step": step,
                            "todo_offset": offset,
                            "todo_id": active_id,
                            "todo_title": active_title,
                        },
                    )
                    _emit_agent_update(
                        runtime,
                        step=step,
                        stage="validation",
                        action="focused",
                        summary=f"Now validating {active_id}: {active_title}.",
                        details={"todo_offset": offset},
                        todo_id=active_id,
                        todo_title=active_title,
                    )

            current_question = str(snapshot.get("current_question", "")).strip()
            if current_question and current_question != previous_question:
                active = None
                if 0 <= offset < len(todos):
                    active = todos[offset]
                active_id = str(active.get("id", "")).strip() if active else ""
                active_title = str(active.get("title", "")).strip() if active else ""
                _emit_reasoning(
                    runtime,
                    step=step,
                    phase="customer_interview",
                    message=f"Interview question drafted: {current_question}",
                    todo_id=active_id or None,
                    todo_title=active_title or None,
                )
                _emit(
                    runtime,
                    "interview.question_drafted",
                    {
                        "step": step,
                        "todo_id": active_id,
                        "todo_title": active_title,
                        "question": current_question,
                    },
                )
                _emit_agent_update(
                    runtime,
                    step=step,
                    stage="interview",
                    action="question_drafted",
                    summary=f"Drafted interview question for {active_id or 'active todo'}.",
                    details={"question": current_question},
                    todo_id=active_id or None,
                    todo_title=active_title or None,
                )
            previous_question = current_question

            for todo in snapshot.get("todos", []):
                todo_id = str(todo.get("id", "")).strip()
                todo_title = str(todo.get("title", "")).strip() or todo_id
                history = todo.get("interview_messages", [])
                prev_count = previous_counts.get(todo_id, 0)
                for idx in range(prev_count, len(history)):
                    message = history[idx]
                    role = str(message.get("role", ""))
                    content = str(message.get("content", ""))
                    _emit(
                        runtime,
                        "interview.message",
                        {
                            "step": step,
                            "todo_id": todo_id,
                            "todo_title": todo_title,
                            "message_index": idx,
                            "role": role,
                            "content": content,
                            "status": todo.get("status"),
                        },
                    )
                    _emit(
                        runtime,
                        "interview.transcript",
                        {
                            "step": step,
                            "todo_id": todo_id,
                            "todo_title": todo_title,
                            "message_index": idx,
                            "role": role,
                            "content": content,
                            "status": _normalize_todo_status(todo.get("status")),
                        },
                    )
                    transcript = _transcript_payload(history[: idx + 1])
                    _emit(
                        runtime,
                        "interview.transcript.updated",
                        {
                            "step": step,
                            "todo_id": todo_id,
                            "todo_title": todo_title,
                            "status": _normalize_todo_status(todo.get("status")),
                            "latest_message_index": idx,
                            "latest_message_role": role,
                            "latest_message_content": content,
                            "transcript": transcript,
                        },
                    )
                    if role == "assistant":
                        _emit_reasoning(
                            runtime,
                            step=step,
                            phase="customer_interview",
                            message=f"Interviewer asked a follow-up for {todo_id}.",
                            todo_id=todo_id,
                            todo_title=todo_title,
                        )
                        _emit_agent_update(
                            runtime,
                            step=step,
                            stage="interview",
                            action="question_asked",
                            summary=f"Interviewer asked a question for {todo_id}.",
                            details={"message_index": idx, "content": content},
                            todo_id=todo_id,
                            todo_title=todo_title,
                        )
                    elif role == "user":
                        _emit_reasoning(
                            runtime,
                            step=step,
                            phase="customer_interview",
                            message=f"Customer response captured for {todo_id}; updating validation confidence.",
                            todo_id=todo_id,
                            todo_title=todo_title,
                        )
                        _emit_agent_update(
                            runtime,
                            step=step,
                            stage="interview",
                            action="response_captured",
                            summary=f"Captured stakeholder response for {todo_id}.",
                            details={"message_index": idx, "content": content},
                            todo_id=todo_id,
                            todo_title=todo_title,
                        )
                previous_counts[todo_id] = len(history)

        runtime.final_state = final_state
        runtime.status = "completed"
        runtime.completed_at = time.time()
        _emit_reasoning(
            runtime,
            step=step,
            phase="thinking",
            message="Interview validation completed across items. Drafting final recommendation.",
        )
        _emit_agent_update(
            runtime,
            step=step,
            stage="synthesis",
            action="started",
            summary="All item interviews complete. Drafting final recommendation.",
        )
        _emit(
            runtime,
            "final.response",
            {
                "steps": step,
                "final_answer": final_state.get("final_answer", ""),
                "state": _state_summary(final_state),
            },
        )
        _emit_agent_update(
            runtime,
            step=step,
            stage="synthesis",
            action="completed",
            summary="Final recommendation generated.",
            details={"final_answer": final_state.get("final_answer", "")},
        )
        _emit(
            runtime,
            "simulation.completed",
            {
                "steps": step,
                "final_answer": final_state.get("final_answer", ""),
                "state": _state_summary(final_state),
            },
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("Simulation failed")
        runtime.status = "failed"
        runtime.completed_at = time.time()
        runtime.error = str(exc)
        _emit_agent_update(
            runtime,
            step=0,
            stage="error",
            action="failed",
            summary="Simulation failed before completion.",
            details={"message": str(exc)},
        )
        _emit(runtime, "simulation.error", {"message": str(exc)})


def _sse_encode(event: dict[str, Any], event_id: int) -> str:
    data = json.dumps(event, ensure_ascii=False)
    event_name = event.get("event", "message")
    return f"id: {event_id}\nevent: {event_name}\ndata: {data}\n\n"


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stakeholders", response_model=StakeholderCatalogResponse)
def list_stakeholders() -> StakeholderCatalogResponse:
    return StakeholderCatalogResponse(stakeholders=_load_stakeholders())


@app.post("/api/simulations", response_model=StartSimulationResponse, status_code=202)
def start_simulation(request: StartSimulationRequest) -> StartSimulationResponse:
    user_input = _resolve_user_input(idea=request.idea, user_input=request.user_input)
    todo_items = _resolve_todo_items(request.todo_list)
    stakeholder_profile = _resolve_stakeholder_profile(
        stakeholder_id=request.stakeholder_id,
        stakeholder_profile=request.stakeholder_profile,
        customer_persona=request.customer_persona,
    )
    simulation_id = str(uuid.uuid4())
    runtime = SimulationRuntime(simulation_id=simulation_id)

    with SIMULATIONS_LOCK:
        SIMULATIONS[simulation_id] = runtime

    worker = threading.Thread(
        target=_run_simulation,
        args=(runtime, request, user_input, stakeholder_profile, todo_items),
        name=f"simulation-{simulation_id}",
        daemon=True,
    )
    worker.start()

    return StartSimulationResponse(
        simulation_id=simulation_id,
        status=runtime.status,
        events_url=f"/api/simulations/{simulation_id}/events",
        details_url=f"/api/simulations/{simulation_id}",
    )


@app.get("/api/simulations/{simulation_id}", response_model=SimulationStatusResponse)
def get_simulation(simulation_id: str) -> SimulationStatusResponse:
    runtime = SIMULATIONS.get(simulation_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' was not found.")

    final_answer = None
    if runtime.final_state:
        final_answer = runtime.final_state.get("final_answer")

    return SimulationStatusResponse(
        simulation_id=simulation_id,
        status=runtime.status,
        started_at=runtime.started_at,
        completed_at=runtime.completed_at,
        error=runtime.error,
        final_answer=final_answer,
    )


@app.get("/api/simulations/{simulation_id}/events")
async def stream_simulation_events(simulation_id: str):
    runtime = SIMULATIONS.get(simulation_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' was not found.")

    async def event_generator():
        event_id = 0
        yield _sse_encode(
            {
                "event": "sse.connected",
                "timestamp": time.time(),
                "simulation_id": simulation_id,
                "payload": {"status": runtime.status},
            },
            event_id,
        )
        event_id += 1

        while True:
            try:
                event = await asyncio.to_thread(runtime.queue.get, True, 1.0)
            except queue.Empty:
                if runtime.status in {"completed", "failed"} and runtime.queue.empty():
                    break
                continue

            yield _sse_encode(event, event_id)
            event_id += 1

            if event.get("event") in {"simulation.completed", "simulation.error"} and runtime.queue.empty():
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
