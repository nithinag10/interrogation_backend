import asyncio
import json
import logging
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


app = FastAPI(
    title="Interrogation Agent API",
    version="0.1.0",
    description="APIs to manage stakeholder definitions and stream live interview simulations.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
SIMULATIONS: dict[str, SimulationRuntime] = {}
SIMULATIONS_LOCK = threading.Lock()


def _default_state(max_interview_messages: int, user_input: str, stakeholder: str) -> dict[str, Any]:
    return {
        "hypothesis": [],
        "user_input": user_input,
        "stakeholder": stakeholder,
        "hypothesis_offset": 0,
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
    hypotheses = state.get("hypothesis", [])
    return {
        "hypothesis_offset": state.get("hypothesis_offset"),
        "current_question": state.get("current_question"),
        "final_answer": state.get("final_answer"),
        "hypotheses": [
            {
                "id": hyp.get("id"),
                "title": hyp.get("title"),
                "status": hyp.get("status"),
                "root_cause": hyp.get("root_cause"),
                "evidence_count": len(hyp.get("evidence", [])),
                "interview_message_count": len(hyp.get("interview_messages", [])),
            }
            for hyp in hypotheses
        ],
    }


def _normalize_hypothesis_status(status: Any) -> str:
    value = str(status or "")
    if value == "validated":
        return "validated"
    if value == "invalidated":
        return "invalidated"
    if value == "cannot_validate":
        return "cannot_validate"
    if value == "in_progress":
        return "in_progress"
    return "pending"


def _emit_reasoning(
    runtime: SimulationRuntime,
    step: int,
    phase: str,
    message: str,
    hypothesis_id: str | None = None,
    hypothesis_title: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "step": step,
        "phase": phase,
        "message": message,
    }
    if hypothesis_id:
        payload["hypothesis_id"] = hypothesis_id
    if hypothesis_title:
        payload["hypothesis_title"] = hypothesis_title
    _emit(runtime, "reasoning.step", payload)


def _run_simulation(
    runtime: SimulationRuntime,
    request: StartSimulationRequest,
    user_input: str,
    stakeholder_profile: str,
) -> None:
    try:
        graph = build_graph()
        state = _default_state(
            max_interview_messages=request.max_interview_messages,
            user_input=user_input,
            stakeholder=stakeholder_profile,
        )
        _emit(
            runtime,
            "simulation.started",
            {
                "max_interview_messages": request.max_interview_messages,
                "user_input": user_input,
            },
        )
        _emit_reasoning(
            runtime,
            step=0,
            phase="thinking",
            message="Simulation started. Analyzing idea and preparing candidate hypotheses.",
        )

        step = 0
        known_hypotheses: set[str] = set()
        previous_status: dict[str, str] = {}
        previous_counts: dict[str, int] = {}
        previous_question: str = ""
        previous_offset: int = -1
        final_state = state
        for snapshot in graph.stream(state, stream_mode="values"):
            step += 1
            final_state = snapshot
            _emit(runtime, "simulation.step", {"step": step, "state": _state_summary(snapshot)})

            hypotheses = snapshot.get("hypothesis", [])

            if hypotheses and not known_hypotheses:
                _emit_reasoning(
                    runtime,
                    step=step,
                    phase="thinking",
                    message=f"Hypothesis distillation completed. Generated {len(hypotheses)} hypotheses.",
                )

            for hypothesis in hypotheses:
                hyp_id = str(hypothesis.get("id", "")).strip()
                if not hyp_id:
                    continue
                hyp_title = str(hypothesis.get("title", "")).strip() or hyp_id
                status = _normalize_hypothesis_status(hypothesis.get("status"))

                if hyp_id not in known_hypotheses:
                    known_hypotheses.add(hyp_id)
                    _emit(
                        runtime,
                        "hypothesis.created",
                        {
                            "step": step,
                            "hypothesis_id": hyp_id,
                            "hypothesis_title": hyp_title,
                            "hypothesis_description": hypothesis.get("description", ""),
                            "status": status,
                        },
                    )
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="hypothesis_created",
                        message=f"Created {hyp_id}: {hyp_title}",
                        hypothesis_id=hyp_id,
                        hypothesis_title=hyp_title,
                    )

                prev_status = previous_status.get(hyp_id)
                if prev_status != status:
                    _emit(
                        runtime,
                        "hypothesis.validation",
                        {
                            "step": step,
                            "hypothesis_id": hyp_id,
                            "hypothesis_title": hyp_title,
                            "status": status,
                            "root_cause": hypothesis.get("root_cause", ""),
                            "evidence_count": len(hypothesis.get("evidence", [])),
                        },
                    )
                    if status == "in_progress":
                        message = f"Validating {hyp_id}: gathering customer evidence."
                    elif status == "validated":
                        message = f"{hyp_id} validated with sufficient interview evidence."
                    elif status == "invalidated":
                        message = f"{hyp_id} invalidated based on interview evidence."
                    elif status == "cannot_validate":
                        message = f"{hyp_id} marked cannot_validate due to insufficient or low-quality evidence."
                    else:
                        message = f"{hyp_id} queued for validation."
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="hypothesis_validation",
                        message=message,
                        hypothesis_id=hyp_id,
                        hypothesis_title=hyp_title,
                    )
                previous_status[hyp_id] = status

            offset = int(snapshot.get("hypothesis_offset", -1))
            if offset != previous_offset:
                previous_offset = offset
                if 0 <= offset < len(hypotheses):
                    active = hypotheses[offset]
                    active_id = str(active.get("id", "")).strip()
                    active_title = str(active.get("title", "")).strip() or active_id
                    _emit_reasoning(
                        runtime,
                        step=step,
                        phase="thinking",
                        message=f"Focused validation on {active_id}: {active_title}.",
                        hypothesis_id=active_id,
                        hypothesis_title=active_title,
                    )

            current_question = str(snapshot.get("current_question", "")).strip()
            if current_question and current_question != previous_question:
                active = None
                if 0 <= offset < len(hypotheses):
                    active = hypotheses[offset]
                active_id = str(active.get("id", "")).strip() if active else ""
                active_title = str(active.get("title", "")).strip() if active else ""
                _emit_reasoning(
                    runtime,
                    step=step,
                    phase="customer_interview",
                    message=f"Interview question drafted: {current_question}",
                    hypothesis_id=active_id or None,
                    hypothesis_title=active_title or None,
                )
            previous_question = current_question

            for hypothesis in snapshot.get("hypothesis", []):
                hyp_id = str(hypothesis.get("id", "")).strip()
                hyp_title = str(hypothesis.get("title", "")).strip() or hyp_id
                history = hypothesis.get("interview_messages", [])
                prev_count = previous_counts.get(hyp_id, 0)
                for idx in range(prev_count, len(history)):
                    message = history[idx]
                    role = str(message.get("role", ""))
                    content = str(message.get("content", ""))
                    _emit(
                        runtime,
                        "interview.message",
                        {
                            "step": step,
                            "hypothesis_id": hyp_id,
                            "hypothesis_title": hyp_title,
                            "message_index": idx,
                            "role": role,
                            "content": content,
                            "status": hypothesis.get("status"),
                        },
                    )
                    _emit(
                        runtime,
                        "interview.transcript",
                        {
                            "step": step,
                            "hypothesis_id": hyp_id,
                            "hypothesis_title": hyp_title,
                            "message_index": idx,
                            "role": role,
                            "content": content,
                            "status": _normalize_hypothesis_status(hypothesis.get("status")),
                        },
                    )
                    if role == "assistant":
                        _emit_reasoning(
                            runtime,
                            step=step,
                            phase="customer_interview",
                            message=f"Interviewer asked a follow-up for {hyp_id}.",
                            hypothesis_id=hyp_id,
                            hypothesis_title=hyp_title,
                        )
                    elif role == "user":
                        _emit_reasoning(
                            runtime,
                            step=step,
                            phase="customer_interview",
                            message=f"Customer response captured for {hyp_id}; updating validation confidence.",
                            hypothesis_id=hyp_id,
                            hypothesis_title=hyp_title,
                        )
                previous_counts[hyp_id] = len(history)

        runtime.final_state = final_state
        runtime.status = "completed"
        runtime.completed_at = time.time()
        _emit_reasoning(
            runtime,
            step=step,
            phase="thinking",
            message="Interview validation completed across hypotheses. Drafting final recommendation.",
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
        _emit(runtime, "simulation.error", {"message": str(exc)})


def _sse_encode(event: dict[str, Any], event_id: int) -> str:
    data = json.dumps(event, ensure_ascii=False)
    event_name = event.get("event", "message")
    return f"id: {event_id}\nevent: {event_name}\ndata: {data}\n\n"


@app.on_event("startup")
def _startup() -> None:
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
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
        args=(runtime, request, user_input, stakeholder_profile),
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
