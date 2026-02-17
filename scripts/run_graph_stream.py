#!/usr/bin/env python3
import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph import build_graph
from app.observability import enable_langsmith, is_langsmith_configured


def _default_state(max_interview_messages: int, user_input: str, stakeholder: str) -> dict:
    return {
        "hypothesis": [],
        "user_input": user_input,
        "stakeholder": stakeholder,
        "hypothesis_offset": 0,
        "final_answer": "",
        "max_interview_messages": max_interview_messages,
        "current_question": "",
    }


def _print_progress(step: int, state: dict) -> None:
    print(f"\n[step {step}]")
    print(f"  hypothesis_offset={state.get('hypothesis_offset')}")
    cq = state.get("current_question", "") or "(none)"
    print(f"  current_question={cq}")
    hypotheses = state.get("hypothesis", [])
    if hypotheses:
        statuses = [f"{h['id']}:{h['status']}" for h in hypotheses]
        print(f"  hypothesis_statuses={', '.join(statuses)}")
    if state.get("final_answer"):
        print("  final_answer generated")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream and observe interrogation graph execution.")
    parser.add_argument("--dry-run", action="store_true", help="Use fake nodes (no LLM/API calls).")
    parser.add_argument(
        "--max-interview-messages",
        type=int,
        default=8,
        help="Max message count before checkpoint ends a hypothesis.",
    )
    parser.add_argument(
        "--user-input",
        type=str,
        default=(
            "Our B2B SaaS trial-to-paid conversion dropped from 22% to 14% after we changed "
            "onboarding. We need to understand what is causing the drop and what to fix first."
        ),
        help="Problem statement.",
    )
    parser.add_argument(
        "--stakeholder",
        type=str,
        default=(
            "VP Product at a mid-market B2B SaaS company. Priorities: conversion, retention, "
            "sales handoff quality. Constraints: small engineering bandwidth this quarter."
        ),
        help="Stakeholder profile.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Enable LangSmith tracing for this run.",
    )
    parser.add_argument(
        "--langsmith-project",
        type=str,
        default="interrogation-agent",
        help="LangSmith project name when --trace is enabled.",
    )
    parser.add_argument(
        "--langsmith-endpoint",
        type=str,
        default="",
        help="Optional LangSmith endpoint override.",
    )
    parser.add_argument(
        "--run-name",
        type=str,
        default="interrogation-simulation",
        help="Run name shown in traces.",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default="simulation,stream",
        help="Comma-separated tags added to the trace run.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(dotenv_path=".env")
    if args.trace:
        enable_langsmith(
            project=args.langsmith_project,
            endpoint=args.langsmith_endpoint or None,
        )
        if is_langsmith_configured():
            print(f"LangSmith tracing enabled (project={args.langsmith_project})")
        else:
            print("LangSmith tracing requested but LANGSMITH_API_KEY is missing.")

    app = _build_dry_run_graph() if args.dry_run else build_graph()
    state = _default_state(
        max_interview_messages=args.max_interview_messages,
        user_input=args.user_input,
        stakeholder=args.stakeholder,
    )

    run_config = {
        "run_name": args.run_name,
        "tags": [tag.strip() for tag in args.tags.split(",") if tag.strip()],
        "metadata": {
            "dry_run": args.dry_run,
            "max_interview_messages": args.max_interview_messages,
        },
    }

    print(f"Streaming run started (dry_run={args.dry_run})")
    step = 0
    final_state = state
    for snapshot in app.stream(state, stream_mode="values", config=run_config):
        step += 1
        final_state = snapshot
        _print_progress(step=step, state=snapshot)

    print("\n=== Final Result ===")
    print(final_state.get("final_answer", "(empty final answer)"))
    print("\nHypotheses:")
    for h in final_state.get("hypothesis", []):
        print(f"- {h['id']} | {h['status']} | {h['title']}")


if __name__ == "__main__":
    main()
