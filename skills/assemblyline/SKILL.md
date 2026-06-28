---
name: assemblyline
description: Build and maintain Codex assembly-line scripts that run repeated Codex steps, deterministic checks, and review loops from normal Python. Use when creating a target-repo script under assembly-lines/, packaging task-specific CodexStep workflows, using the bundled assemblyline Python primitives, or running the included ReLU toy example.
---

# Assemblyline

Assemblyline provides small Python primitives for code-owned Codex loops. Use it to write explicit scripts that call Codex steps, run deterministic checks, capture artifacts, and decide whether to repair or stop.

## Create A Line

Copy `assets/templates/assembly_line.py` into the target repo as `assembly-lines/<task>_line.py`, and copy `assets/templates/line_steps.py` next to it. Keep these files in the target repo; do not edit the target repo `.gitignore` just to add assemblyline.

Require callers to set `ASSEMBLYLINE_SKILL_DIR` to this skill directory:

```bash
export ASSEMBLYLINE_SKILL_DIR=/path/to/skills/assemblyline
python3 assembly-lines/<task>_line.py
```

The template prepends `$ASSEMBLYLINE_SKILL_DIR/lib/python` to `sys.path` and imports the bundled `assemblyline` package from there. Keep that bootstrap in generated scripts so target repos do not need to vendor or install the library.

The template accepts optional `--log-level {quiet,info,debug}` to choose terminal progress logging; the template default is `info`. Full prompts, raw Codex JSONL, command output, and decisions remain in run artifacts.

## Use The Primitives

Use these public names from `assemblyline`:

- `TaskSpec` for task metadata, acceptance criteria, and prompt input.
- `RunStore` for `.runs/<run-id>/` events and artifacts.
- `RunContext` to pass task, repo root, run id, store, and required terminal logging level.
- `TerminalLogLevel` to control library lifecycle logs through `RunContext.terminal_logging`.
- `LineOutcome` for approved, rejected, and failed line results.
- `ShellCheck` for deterministic commands.
- `ShellCheckResult` for completed deterministic command results and prompt-safe check summaries via `as_prompt_input()`.
- `CodexStep` for one Codex `exec` invocation.
- `CodexStepResult` for successful Codex step results.
- `CodexStepError` for completed nonzero Codex exits.

Call `RunStore.start(task)` once at the beginning of a run and `RunStore.finish(outcome)` once at the end. Scripts own terminal result printing; the store only writes stable run lifecycle events.

Use `step.*` event names and `steps/<name>/` artifacts.

Use line-specific step libraries for higher-level steps. The template's `line_steps.py` and the ReLU example's `relu_steps.py` show a `Review` wrapper that composes `CodexStep`, owns the review JSON schema, and returns a `ReviewResult` with `review_ok` and `as_prompt_input()`. `Review` is not a core `assemblyline` primitive.

For API details, read `references/library-api.md`. For a complete runnable example, inspect `examples/relu_line.py`, which solves the bundled failing C++ ReLU task in `examples/toy-tasks/toy-ml`.
