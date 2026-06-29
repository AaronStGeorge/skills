#!/usr/bin/env python3
from __future__ import annotations

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
from builds import toy_ml
from relu_steps import Review


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR / "examples"
TOY_ML_SOURCE = REPO_ROOT / "toy-tasks" / "toy-ml"


def main() -> int:
    task = TaskSpec(
        id="toy-ml-relu",
        title="Implement ReLU for packed float tensors",
        target_path="toy-tasks/toy-ml",
        instructions=(
            "Implement toy::relu for the tiny C++ packed-tensor library. Keep the existing "
            "Tensor API and tests intact unless a test exposes a real scaffold bug."
        ),
        acceptance_criteria=[
            "CMake configure succeeds.",
            "The C++ target builds successfully.",
            "CTest passes the prewritten ReLU tests.",
            "relu preserves shape, clamps negative values to zero, keeps zero and positive values, and does not mutate the input tensor.",
        ],
        context_paths=[
            "toy-tasks/toy-ml/include/toy/tensor.h",
            "toy-tasks/toy-ml/src/tensor.cpp",
            "toy-tasks/toy-ml/src/relu.cpp",
            "toy-tasks/toy-ml/tests/relu_tests.cpp",
        ],
    )

    store = RunStore(REPO_ROOT)
    ctx = RunContext(
        task=task,
        repo_root=REPO_ROOT,
        run_id=store.run_id,
        store=store,
        terminal_logging=TerminalLogLevel.INFO,
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
    knobs = toy_ml.ToyMlKnobs(source_dir=str(TOY_ML_SOURCE))

    baseline = toy_ml.build(knobs)
    if not baseline.built:
        return LineOutcome.failed(
            reason="baseline_unavailable",
            message="Baseline build did not succeed before running Codex.",
            details={"baseline": baseline.as_prompt_input()},
        )
    baseline_test = run_tests(ctx, "baseline-test", baseline.build_path)
    if baseline_test.ok:
        return LineOutcome.failed(
            reason="baseline_already_solved",
            message="Baseline tests already passed; the toy task is already solved.",
            details={
                "baseline": baseline.as_prompt_input(),
                "test": baseline_test.as_prompt_input(),
            },
        )

    maker = CodexStep("maker", "workspace-write", build_maker_prompt)
    maker.run(
        ctx,
        {"baseline": baseline.as_prompt_input(), "test": baseline_test.as_prompt_input()},
    )
    ctx.store.capture_git_diff("after-maker.patch", paths=[ctx.task.target_path])

    after = toy_ml.build(knobs)
    if not after.built:
        return LineOutcome.failed(
            reason="post_build_failed",
            message="Post-maker build did not succeed.",
            details={"build": after.as_prompt_input()},
        )
    after_test = run_tests(ctx, "after-maker-test", after.build_path)
    if not after_test.ok:
        return LineOutcome.failed(
            reason="post_tests_failed",
            message="Post-maker tests did not pass.",
            details={"build": after.as_prompt_input(), "test": after_test.as_prompt_input()},
        )

    review = Review("review-after-maker", build_review_prompt)
    result = review.run(
        ctx,
        {
            "build": after.as_prompt_input(),
            "test": after_test.as_prompt_input(),
            "patch_name": "after-maker.patch",
            "patch": read_artifact(ctx.store, "after-maker.patch"),
        },
    )
    details = {
        "build": after.as_prompt_input(),
        "test": after_test.as_prompt_input(),
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


def run_tests(ctx: RunContext, name: str, build_path: Path) -> ShellCheckResult:
    return ShellCheck(
        name,
        ["ctest", "--test-dir", str(build_path), "--output-on-failure"],
    ).run(ctx)


def read_artifact(store: RunStore, relative_path: str) -> str:
    path = store.artifact_path(relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_maker_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the maker step in a tiny assembly-line script.

Task:
{ctx.task.as_prompt_input()}

Baseline build:
{inputs.get("baseline")}

Baseline tests (currently failing):
{inputs.get("test")}

Implement the requested change in the repository. Keep the change focused under {ctx.task.target_path}.
Run or reason against the CMake build and CTest before finishing.
"""


def build_review_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the review step in a tiny assembly-line script.

Review the completed task without editing files.

Task:
{ctx.task.as_prompt_input()}

Build result:
{inputs.get("build")}

Test result:
{inputs.get("test")}

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
