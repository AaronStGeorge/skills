# Assemblyline Library API

Read this when writing or changing non-trivial assembly-line scripts.

## Imports

`assemblyline` is installed (editable) from the workspace shared library at `lib/python/`:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e lib/python
```

Scripts then import it directly — no `sys.path` bootstrap and no `ASSEMBLYLINE_SKILL_DIR`:

```python
from assemblyline import (
    CodexStep,
    CodexStepError,
    CodexStepResult,
    LineOutcome,
    RunContext,
    RunStore,
    ShellCheck,
    ShellCheckResult,
    TaskSpec,
    TerminalLogLevel,
)
```

The same install exposes the build libraries `buildlib` (the `BuildKnobs` and `BuildResult` typed dataclass bases, plus `resolve_source_dir` and `build_dir`) and `builds` (one `build(knobs, *deps)` function per project, e.g. `from builds.toy_ml import build`).

The template accepts optional `--log-level {quiet,info,debug}`, defaults to `TerminalLogLevel.INFO`, and lets `argparse` fail clearly for invalid values. This is template policy; direct `RunContext` construction must pass `terminal_logging` explicitly.

## Core Types

- `TaskSpec`: immutable task definition. `as_prompt_input()` returns prompt-ready task text.
- `LineOutcome`: immutable line result with `status`, `reason`, `message`, `details`, `ok`, `exit_code`, and `as_event()`. Use `LineOutcome.approved(...)`, `.rejected(...)`, `.failed(...)`, and `.failed_exception(...)`.
- `TerminalLogLevel`: `StrEnum` with `quiet`, `info`, and `debug`.
- `RunStore`: owns `.runs/<run-id>/`, `events.jsonl`, artifact writes, check dirs, step dirs, git diff capture, `start(task)`, and `finish(outcome)`.
- `RunContext`: immutable bundle of `task`, `repo_root`, `run_id`, `store`, and required `terminal_logging`.
- `ShellCheck`: runs a deterministic command and writes `checks/<name>/output.log` plus `result.json` only when the command completes.
- `ShellCheckResult`: completed shell command result with `exit_code`, `ok`, `output`, and `as_prompt_input()`.
- `CodexStep`: runs `codex exec` with a generated prompt and writes `steps/<name>/prompt.md`, `output.jsonl`, `last_message.txt`, and optional `output_schema.json`.
- `CodexStepResult`: successful Codex step result with `output` and `last_message`.
- `CodexStepError`: raised for completed nonzero Codex exits. `as_event_details()` returns argv, cwd, sandbox, exit code, elapsed time, relative artifact paths, and a bounded output tail.

`RunStore.start(task)` appends `run.start` with JSON-safe task metadata. `RunStore.finish(outcome)` appends `run.finish` with `outcome.as_event()`. Neither prints. Scripts should print their own final status after calling `finish(...)`.

`LineOutcome.failed_exception(exc, ...)` creates a failed outcome with optional caller details, `exc.as_event_details()` when present, `exception_type`, `error`, and `traceback`.

`LineOutcome.as_event()` always returns:

```python
{"status": status, "reason": reason, "message": message, "details": details}
```

`details` is JSON-normalized with `default=str`, so values like `Path` are stable in event payloads.

## Command Behavior

`ShellCheck.run(ctx)` returns `ShellCheckResult` for any completed command, including nonzero exits. Missing executables and timeouts append `check.error`, write no completed result artifacts, log an error, and re-raise the original exception.

`CodexStep.run(ctx, inputs)` returns `CodexStepResult` only when Codex exits with code 0. Completed nonzero Codex exits write available output artifacts, append `step.error`, log an error, and raise `CodexStepError`. Missing executables and timeouts append `step.error`, log an error, and re-raise the original exception.

Review JSON parsing, approval policy, and retry orchestration belong in line-specific step libraries and line scripts, not in the core library.

## Line-Specific Step Libraries

Create small local modules for higher-level steps that are specific to a line or family of lines. The template's `line_steps.py` and the ReLU example's `relu_steps.py` demonstrate this shape with `Review`.

`Review(name, build_prompt)` composes a read-only `CodexStep` with the standard review output schema:

```json
{"approved": true, "summary": "ok", "issues": [], "required_fixes": []}
```

`Review.run(ctx, inputs)` returns a `ReviewResult` instead of a raw `CodexStepResult`. `ReviewResult` has `review_ok`, `summary`, `issues`, `required_fixes`, and `as_prompt_input()`.

The wrapper relies on Codex `--output-schema` for the normal path. Schema-shaped review JSON sets `review_ok` from the boolean `approved` field. Malformed JSON or schema mismatches raise `ValueError` and flow through the line's top-level `LineOutcome.failed_exception(...)` handling. A nonzero Codex process is still normal `CodexStep` behavior and raises `CodexStepError`.

Line outcome conversion should branch on `review_ok`, using `review_rejected` and `review_approved`. Review artifacts and events remain the underlying `CodexStep` artifacts and events under `steps/<review-name>/` with `step.*` event names.

## Terminal Logging

Library lifecycle logs are written to `stderr` according to `RunContext.terminal_logging`:

- `quiet`: no library lifecycle logs.
- `info`: start and finish lines for each completed `ShellCheck` and successful `CodexStep`, including name, pass/fail status on finish, exit code, elapsed time, and artifact paths. Error events are also logged.
- `debug`: `info` plus argv/cwd/sandbox detail and bounded snippets. `ShellCheck` prints the command output tail. `CodexStep` prints the prompt at start and last message at successful finish. Raw Codex JSONL stays file-only.

## Events And Artifacts

Use these event names:

- `run.start`, `run.finish`, and `run.diff_captured`
- `check.start`, `check.finish`, and `check.error`
- `step.start`, `step.finish`, and `step.error`

Use `checks/` for shell check artifacts and `steps/` for Codex step artifacts. Do not generate or consume earlier artifact paths or event names.

## Script Pattern

Keep scripts explicit:

1. Build a `TaskSpec`.
2. Create `RunStore(REPO_ROOT)` and `RunContext`.
3. Call `RunStore.start(task)`.
4. Run deterministic baseline checks.
5. Return `LineOutcome.failed(...)` if the baseline is unavailable or invalid for the task.
6. Run a maker `CodexStep` with a focused prompt.
7. Capture diffs for task paths.
8. Re-run deterministic checks and return `LineOutcome.failed(...)` if they fail.
9. Run a review step from the line-specific step library and convert its `ReviewResult` to `LineOutcome.approved(...)`, `.rejected(...)`, or `.failed(...)`.
10. Catch unexpected exceptions at the top level with `LineOutcome.failed_exception(exc)`.
11. Call `RunStore.finish(outcome)`, print the script-owned terminal result, and return `outcome.exit_code`.
