#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from assemblyline import (
    CodexStep,
    LineOutcome,
    RunContext,
    RunStore,
    ShellCheck,
    ShellCheckResult,
    TaskSpec,
    TerminalLogLevel,
)
from line_steps import Review


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the assembly line.")
    parser.add_argument(
        "--log-level",
        choices=[level.value for level in TerminalLogLevel],
        default=TerminalLogLevel.INFO.value,
        help="terminal progress logging level (default: info)",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    task = TaskSpec(
        id="replace-me",
        title="Replace me",
        target_path=".",
        instructions="Replace this with the task-specific implementation request.",
        acceptance_criteria=[
            "Replace this with task-specific acceptance criteria.",
        ],
        context_paths=[],
    )

    store = RunStore(REPO_ROOT)
    ctx = RunContext(
        task=task,
        repo_root=REPO_ROOT,
        run_id=store.run_id,
        store=store,
        terminal_logging=TerminalLogLevel(args.log_level),
    )
    store.start(task)

    try:
        outcome = run_line(ctx)
    except Exception as exc:
        outcome = LineOutcome.failed_exception(exc)

    store.finish(outcome)
    print_run_result(outcome, store)
    return outcome.exit_code


def run_line(ctx: RunContext) -> LineOutcome:
    maker = CodexStep("maker", "workspace-write", build_maker_prompt)
    maker.run(ctx)

    ctx.store.capture_git_diff("after-maker.patch", paths=[ctx.task.target_path])

    post_checks = run_deterministic_checks(ctx, "after-maker")
    if not checks_ok(post_checks):
        return LineOutcome.failed(
            reason="post_checks_failed",
            message="Post-maker deterministic checks did not pass.",
            details={"checks": summarize_checks(post_checks)},
        )

    review = Review("review", build_review_prompt)
    result = review.run(
        ctx,
        {
            "checks": summarize_checks(post_checks),
            "patch_name": "after-maker.patch",
            "patch": read_artifact(ctx.store, "after-maker.patch"),
        },
    )
    details = {
        "checks": summarize_checks(post_checks),
        "review": result.as_prompt_input(),
    }
    if not result.review_ok:
        return LineOutcome.rejected(
            reason="review_rejected",
            message=result.summary or "Review rejected the change.",
            details=details,
        )
    return LineOutcome.approved(
        reason="review_approved",
        message=result.summary or "Review approved the change.",
        details=details,
    )


def run_deterministic_checks(ctx: RunContext, label: str) -> dict[str, ShellCheckResult]:
    return {
        "replace-me": ShellCheck(
            f"{label}-replace-me",
            [sys.executable, "-c", "print('replace deterministic check')"],
        ).run(ctx)
    }


def checks_ok(checks: Mapping[str, ShellCheckResult]) -> bool:
    return all(check.ok for check in checks.values())


def summarize_checks(checks: Mapping[str, ShellCheckResult]) -> dict[str, Any]:
    return {key: value.as_prompt_input() for key, value in checks.items()}


def read_artifact(store: RunStore, relative_path: str) -> str:
    path = store.artifact_path(relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_maker_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the maker step in an assembly-line script.

Task:
{ctx.task.as_prompt_input()}

Implement the requested change. Keep the change focused under {ctx.task.target_path}.
Run or reason against the deterministic checks before finishing.
"""


def build_review_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the review step in an assembly-line script.

Review the completed task without editing files.

Task:
{ctx.task.as_prompt_input()}

Deterministic checks:
{inputs.get("checks")}

Patch artifact: {inputs.get("patch_name")}
```diff
{inputs.get("patch", "")}
```

Return JSON matching the provided schema. Set approved to true only if the tests passed and the implementation is correct, focused, and satisfies every acceptance criterion.
"""


def print_run_result(outcome: LineOutcome, store: RunStore) -> None:
    stream = sys.stdout if outcome.ok else sys.stderr
    print(f"Run {outcome.status}: {outcome.message}", file=stream)
    print(f"Reason: {outcome.reason}", file=stream)
    print(f"Artifacts: {store.run_dir}", file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
