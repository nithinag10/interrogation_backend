#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph import build_graph
from app.observability import enable_langsmith


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _build_dry_run_graph():
    class FakeDistillationNode:
        def run(self, state):
            state["hypothesis"] = [
                {
                    "id": "h-1",
                    "title": "Onboarding complexity hurt conversion",
                    "description": "Simpler onboarding will improve trial-to-paid conversion.",
                    "status": "pending",
                    "root_cause": "",
                    "evidence": [],
                    "interview_messages": [],
                }
            ]
            state["hypothesis_offset"] = 0
            state["current_question"] = ""
            return state

    class FakeInterrogationNode:
        def run(self, state):
            h = state["hypothesis"][state["hypothesis_offset"]]
            if len(h["interview_messages"]) >= 2:
                h["status"] = "validated"
                h["root_cause"] = "Onboarding friction increases abandonment before value realization."
                h["evidence"].append("Pilot users converted better with simpler onboarding.")
                state["current_question"] = ""
                return state
            question = "What evidence shows onboarding complexity changed conversion?"
            h["status"] = "in_progress"
            h["interview_messages"].append({"role": "assistant", "content": question})
            state["current_question"] = question
            return state

    class FakeStakeholderNode:
        def run(self, state):
            h = state["hypothesis"][state["hypothesis_offset"]]
            h["interview_messages"].append(
                {"role": "user", "content": "A/B test showed +12% conversion after simplification."}
            )
            state["current_question"] = ""
            return state

    class FakeBusinessExpertNode:
        def run(self, state):
            state["final_answer"] = (
                "Conversion drop appears linked to onboarding complexity. "
                "Prioritize simplifying onboarding first."
            )
            return state

    return build_graph(
        distillation_node=FakeDistillationNode(),
        interrogation_node=FakeInterrogationNode(),
        stakeholder_node=FakeStakeholderNode(),
        business_expert_node=FakeBusinessExpertNode(),
    )


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input dataset file was not found: {path}")

    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {index}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"JSONL line {index} must be a JSON object.")
            rows.append(item)
        return rows

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JSON dataset must be an array of objects.")
    for idx, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Dataset item at index {idx} must be a JSON object.")
    return payload


def _resolve_idea(case: dict[str, Any]) -> str:
    value = str(case.get("idea", "")).strip()
    if value:
        return value
    raise ValueError("Missing required field: idea")


def _resolve_customer_persona(case: dict[str, Any]) -> str:
    value = str(case.get("customer_persona", "")).strip()
    if value:
        return value
    raise ValueError("Missing required field: customer_persona")


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "unknown"))
        content = str(message.get("content", "")).strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _status_counts(hypotheses: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hyp in hypotheses:
        status = str(hyp.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _run_case(
    graph: Any,
    case: dict[str, Any],
    default_max_messages: int,
    index: int,
) -> dict[str, Any]:
    started_at = _utc_now()
    wall_start = time.perf_counter()
    run_id = str(uuid.uuid4())
    case_id = str(case.get("case_id", "")).strip() or f"case-{index}"

    idea = _resolve_idea(case)
    customer_persona = _resolve_customer_persona(case)

    max_messages_raw = case.get("max_interview_messages", default_max_messages)
    max_messages = int(max_messages_raw)
    if max_messages < 2:
        raise ValueError("max_interview_messages must be >= 2")

    state = _default_state(
        max_interview_messages=max_messages,
        user_input=idea,
        stakeholder=customer_persona,
    )

    steps = 0
    final_state = state
    for snapshot in graph.stream(state, stream_mode="values"):
        steps += 1
        final_state = snapshot

    hypotheses = final_state.get("hypothesis", [])
    transcript_by_hypothesis: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        messages = hypothesis.get("interview_messages", [])
        transcript_by_hypothesis.append(
            {
                "hypothesis_id": hypothesis.get("id"),
                "hypothesis_title": hypothesis.get("title"),
                "status": hypothesis.get("status"),
                "messages": messages,
                "text": _format_transcript(messages),
            }
        )

    ended_at = _utc_now()
    duration_seconds = round(time.perf_counter() - wall_start, 3)
    return {
        "run_id": run_id,
        "case_id": case_id,
        "timestamps": {
            "started_at": started_at,
            "ended_at": ended_at,
        },
        "input": {
            "idea": idea,
            "customer_persona": customer_persona,
            "max_interview_messages": max_messages,
            "metadata": case.get("metadata", {}),
        },
        "output": {
            "hypothesis": hypotheses,
            "interview_transcript": transcript_by_hypothesis,
            "final_response": final_state.get("final_answer", ""),
        },
        "metrics": {
            "steps": steps,
            "duration_seconds": duration_seconds,
            "hypothesis_count": len(hypotheses),
            "status_counts": _status_counts(hypotheses),
        },
        "error": None,
    }


def _write_results(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".jsonl":
        lines = [json.dumps(item, ensure_ascii=True) for item in results]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return
    path.write_text(json.dumps(results, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run evaluation cases from a local dataset and save detailed outputs for each run."
    )
    parser.add_argument("--input", required=True, help="Path to dataset file (.json or .jsonl).")
    parser.add_argument(
        "--output",
        default="data/evaluation_results.jsonl",
        help="Output path (.jsonl recommended).",
    )
    parser.add_argument(
        "--max-interview-messages",
        type=int,
        default=8,
        help="Default max message limit, overrideable per case.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases (0 means all).")
    parser.add_argument("--fail-fast", action="store_true", help="Stop immediately on first failed case.")
    parser.add_argument("--dry-run", action="store_true", help="Use fake nodes and skip LLM/API calls.")
    parser.add_argument("--trace", action="store_true", help="Enable LangSmith tracing for this run.")
    parser.add_argument("--langsmith-project", default="interrogation-agent", help="LangSmith project name.")
    parser.add_argument("--langsmith-endpoint", default="", help="Optional LangSmith endpoint override.")
    args = parser.parse_args()

    load_dotenv(dotenv_path=".env")
    if args.trace:
        enable_langsmith(
            project=args.langsmith_project,
            endpoint=args.langsmith_endpoint or None,
        )
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    cases = _load_dataset(input_path)
    if args.limit > 0:
        cases = cases[: args.limit]

    graph = _build_dry_run_graph() if args.dry_run else build_graph()

    results: list[dict[str, Any]] = []
    failed = 0
    for index, case in enumerate(cases, start=1):
        try:
            result = _run_case(
                graph=graph,
                case=case,
                default_max_messages=args.max_interview_messages,
                index=index,
            )
        except Exception as exc:
            failed += 1
            result = {
                "run_id": str(uuid.uuid4()),
                "case_id": str(case.get("case_id", "")).strip() or f"case-{index}",
                "timestamps": {
                    "started_at": _utc_now(),
                    "ended_at": _utc_now(),
                },
                "input": case,
                "output": None,
                "metrics": {
                    "steps": 0,
                    "duration_seconds": 0.0,
                    "hypothesis_count": 0,
                    "status_counts": {},
                },
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            }
            if args.fail_fast:
                results.append(result)
                break
        results.append(result)

    _write_results(output_path, results)

    succeeded = len(results) - failed
    print(f"Evaluation finished. total={len(results)} succeeded={succeeded} failed={failed}")
    print(f"Results written to: {output_path}")


if __name__ == "__main__":
    main()
