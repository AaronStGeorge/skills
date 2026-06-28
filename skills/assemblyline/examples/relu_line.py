#!/usr/bin/env python3
from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR / "examples"
LIB_DIR = SKILL_DIR / "lib" / "python"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from assemblyline import (  # noqa: E402
    CodexStep,
    LineOutcome,
    RunContext,
    RunStore,
    ShellCheck,
    ShellCheckResult,
    TaskSpec,
    TerminalLogLevel,
)
from relu_steps import Review  # noqa: E402


CheckMap = dict[str, ShellCheckResult | None]


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
    baseline = run_cmake_triplet(ctx, "baseline", ctx.store.run_dir / "baseline-build")
    if not _check_ok(baseline["configure"]) or not _check_ok(baseline["build"]):
        return LineOutcome.failed(
            reason="baseline_unavailable",
            message="Baseline configure/build did not pass before running Codex.",
            details={"checks": summarize_checks(baseline)},
        )
    if _check_ok(baseline["test"]):
        return LineOutcome.failed(
            reason="baseline_already_solved",
            message="Baseline CTest already passed; the toy task is already solved.",
            details={"checks": summarize_checks(baseline)},
        )

    maker = CodexStep("maker", "workspace-write", build_maker_prompt)
    maker.run(ctx, {"baseline": summarize_checks(baseline)})
    ctx.store.capture_git_diff("after-maker.patch", paths=[ctx.task.target_path])

    final_checks = run_cmake_triplet(ctx, "after-maker", ctx.store.run_dir / "after-maker-build")
    if not checks_ok(final_checks):
        return LineOutcome.failed(
            reason="post_checks_failed",
            message="Post-maker deterministic checks did not pass.",
            details={"checks": summarize_checks(final_checks)},
        )

    review = Review("review-after-maker", build_review_prompt)
    result = review.run(
        ctx,
        {
            "checks": summarize_checks(final_checks),
            "patch_name": "after-maker.patch",
            "patch": read_artifact(ctx.store, "after-maker.patch"),
        },
    )
    details = {
        "checks": summarize_checks(final_checks),
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


def run_cmake_triplet(ctx: RunContext, label: str, build_dir: Path) -> CheckMap:
    source_dir = ctx.repo_root / ctx.task.target_path
    build_dir.mkdir(parents=True, exist_ok=True)
    configure = ShellCheck(
        f"{label}-configure",
        ["cmake", "-S", source_dir, "-B", build_dir],
    ).run(ctx)

    build = ShellCheck(
        f"{label}-build",
        ["cmake", "--build", build_dir],
    ).run(ctx)
    if not configure.ok or not build.ok:
        return {"configure": configure, "build": build, "test": None}

    test = ShellCheck(
        f"{label}-test",
        ["ctest", "--test-dir", build_dir, "--output-on-failure"],
    ).run(ctx)
    return {"configure": configure, "build": build, "test": test}


def checks_ok(checks: CheckMap) -> bool:
    return all(_check_ok(check) for check in checks.values())


def _check_ok(check: ShellCheckResult | None) -> bool:
    return check is not None and check.ok


def summarize_checks(checks: CheckMap) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in checks.items():
        if value is None:
            summary[key] = {
                "ok": False,
                "skipped": True,
                "reason": "Skipped because an earlier check failed.",
            }
            continue
        summary[key] = {
            **value.as_prompt_input(),
            "skipped": False,
        }
    return summary


def read_artifact(store: RunStore, relative_path: str) -> str:
    path = store.artifact_path(relative_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def build_maker_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the maker step in a tiny assembly-line script.

Task:
{ctx.task.as_prompt_input()}

Baseline checks:
{inputs.get("baseline")}

Implement the requested change in the repository. Keep the change focused under {ctx.task.target_path}.
Run or reason against the CMake/CTest checks before finishing.
"""


def build_review_prompt(ctx: RunContext, inputs: Mapping[str, Any]) -> str:
    return f"""You are the review step in a tiny assembly-line script.

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
