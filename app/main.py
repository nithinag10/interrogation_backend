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


def _todo_brief(todo: dict[str, Any]) -> dict[str, Any]:
    todo_id = str(todo.get("id", "")).strip()
    todo_title = str(todo.get("title", "")).strip() or todo_id
    return {
        "todo_id": todo_id,
        "todo_title": todo_title,
        "status": _normalize_todo_status(todo.get("status")),
        "resolution": str(todo.get("resolution", "")).strip(),
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
        _emit(
            runtime,
            "progress.update",
            {
                "step": 0,
                "phase": "thinking",
                "message": "Thinking through the validation flow.",
            },
        )
        _emit(
            runtime,
            "progress.update",
            {
                "step": 0,
                "phase": "creating_todo_list",
                "message": "Creating your todo list.",
            },
        )

        step = 0
        emitted_todo_list = False
        previous_status: dict[str, str] = {}
        previous_counts: dict[str, int] = {}
        previous_offset: int = -1
        final_state = state
        for snapshot in graph.stream(state, stream_mode="values"):
            step += 1
            final_state = snapshot

            todos = snapshot.get("todos", [])

            if todos and not emitted_todo_list:
                emitted_todo_list = True
                _emit(
                    runtime,
                    "todo.list_created",
                    {
                        "step": step,
                        "count": len(todos),
                        "items": [_todo_brief(todo) for todo in todos],
                        "message": "Todo list is ready.",
                    },
                )
                _emit(
                    runtime,
                    "progress.update",
                    {
                        "step": step,
                        "phase": "thinking",
                        "message": "Starting validation on the first todo item.",
                    },
                )

            for todo in todos:
                todo_id = str(todo.get("id", "")).strip()
                if not todo_id:
                    continue
                todo_title = str(todo.get("title", "")).strip() or todo_id
                status = _normalize_todo_status(todo.get("status"))

                prev_status = previous_status.get(todo_id)
                if prev_status != status:
                    if prev_status == "pending" and status == "solved":
                        next_todo = None
                        for candidate in todos:
                            if _normalize_todo_status(candidate.get("status")) != "pending":
                                continue
                            next_todo = candidate
                            break
                        _emit(
                            runtime,
                            "todo.item_completed",
                            {
                                "step": step,
                                "completed": _todo_brief(todo),
                                "next_item": _todo_brief(next_todo) if next_todo else None,
                                "remaining_count": sum(
                                    1
                                    for item in todos
                                    if _normalize_todo_status(item.get("status")) == "pending"
                                ),
                            },
                        )
                previous_status[todo_id] = status


            for todo in todos:
                todo_id = str(todo.get("id", "")).strip()
                if not todo_id:
                    continue
                todo_title = str(todo.get("title", "")).strip() or todo_id
                history = todo.get("interview_messages", [])
                prev_count = previous_counts.get(todo_id, 0)
                for idx in range(prev_count, len(history)):
                    message = history[idx]
                    role = str(message.get("role", ""))
                    content = str(message.get("content", "")).strip()
                    if not content:
                        continue
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
                        },
                    )
                previous_counts[todo_id] = len(history)

            offset = int(snapshot.get("todo_offset", -1))
            if offset != previous_offset:
                previous_offset = offset
                if 0 <= offset < len(todos):
                    active = todos[offset]
                    active_id = str(active.get("id", "")).strip()
                    active_title = str(active.get("title", "")).strip() or active_id
                    _emit(
                        runtime,
                        "todo.item_started",
                        {
                            "step": step,
                            "todo_offset": offset,
                            "todo_id": active_id,
                            "todo_title": active_title,
                            "message": f"Now working on {active_id}: {active_title}.",
                        },
                    )
                    _emit(
                        runtime,
                        "progress.update",
                        {
                            "step": step,
                            "phase": "thinking",
                            "message": f"Thinking through evidence for {active_id}.",
                        },
                    )

        runtime.final_state = final_state
        runtime.status = "completed"
        runtime.completed_at = time.time()
        _emit(
            runtime,
            "progress.update",
            {
                "step": step,
                "phase": "thinking",
                "message": "All todo items processed. Preparing final recommendation.",
            },
        )
        _emit(
            runtime,
            "simulation.completed",
            {
                "steps": step,
                "final_answer": final_state.get("final_answer", ""),
                "next_action": {
                    "type": "run_simulation",
                    "label": "Run Simulation",
                    "message": "Want to tweak your idea and test again? Run simulation.",
                },
                "state": _state_summary(final_state),
            },
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("Simulation failed")
        runtime.status = "failed"
        runtime.completed_at = time.time()
        runtime.error = str(exc)
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
