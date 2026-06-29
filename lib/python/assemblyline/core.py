from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class TerminalLogLevel(StrEnum):
    QUIET = "quiet"
    INFO = "info"
    DEBUG = "debug"


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    target_path: str
    instructions: str
    acceptance_criteria: Sequence[str]
    context_paths: Sequence[str] = ()

    def as_prompt_input(self) -> str:
        criteria = "\n".join(f"- {item}" for item in self.acceptance_criteria)
        context = "\n".join(f"- {item}" for item in self.context_paths)
        return f"""ID: {self.id}
Title: {self.title}
Target path: {self.target_path}
Instructions: {self.instructions}
Acceptance criteria:
{criteria}
Context paths:
{context}
"""


@dataclass(frozen=True)
class LineOutcome:
    status: str
    reason: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"approved", "rejected", "failed"}:
            raise ValueError(f"invalid line outcome status: {self.status}")
        object.__setattr__(self, "details", dict(self.details or {}))

    @classmethod
    def approved(
        cls,
        reason: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "LineOutcome":
        return cls("approved", reason, message, details or {})

    @classmethod
    def rejected(
        cls,
        reason: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "LineOutcome":
        return cls("rejected", reason, message, details or {})

    @classmethod
    def failed(
        cls,
        reason: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> "LineOutcome":
        return cls("failed", reason, message, details or {})

    @classmethod
    def failed_exception(
        cls,
        exc: BaseException,
        reason: str = "unexpected_exception",
        message: str = "Unexpected exception while running the assembly line.",
        details: Mapping[str, Any] | None = None,
    ) -> "LineOutcome":
        merged: dict[str, Any] = dict(details or {})
        as_event_details = getattr(exc, "as_event_details", None)
        if callable(as_event_details):
            merged.update(dict(as_event_details()))
        merged.update(
            {
                "exception_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
            }
        )
        return cls.failed(reason=reason, message=message, details=merged)

    @property
    def ok(self) -> bool:
        return self.status == "approved"

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def as_event(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "details": _json_normalized(self.details),
        }


@dataclass(frozen=True)
class RunContext:
    task: TaskSpec
    repo_root: Path
    run_id: str
    store: "RunStore"
    terminal_logging: TerminalLogLevel


@dataclass(frozen=True)
class ShellCheckResult:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    exit_code: int
    elapsed_s: float
    output_path: Path
    result_path: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return self.output_path.read_text(encoding="utf-8")

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "ok": self.ok,
            "tail": self.output[-tail_chars:],
        }


@dataclass(frozen=True)
class CodexStepResult:
    name: str
    argv: tuple[str, ...]
    elapsed_s: float
    prompt_path: Path
    output_path: Path
    last_message_path: Path

    @property
    def output(self) -> str:
        return self.output_path.read_text(encoding="utf-8")

    @property
    def last_message(self) -> str:
        if not self.last_message_path.exists():
            return ""
        return self.last_message_path.read_text(encoding="utf-8")


class CodexStepError(RuntimeError):
    def __init__(
        self,
        *,
        name: str,
        argv: Sequence[str],
        cwd: Path,
        sandbox: str,
        exit_code: int,
        elapsed_s: float,
        prompt_path: Path,
        output_path: Path,
        last_message_path: Path,
        prompt_artifact: str,
        output_artifact: str,
        last_message_artifact: str,
        output_tail: str,
    ) -> None:
        super().__init__(f"Codex step {name!r} failed with exit code {exit_code}.")
        self.name = name
        self.argv = tuple(argv)
        self.cwd = cwd
        self.sandbox = sandbox
        self.exit_code = exit_code
        self.elapsed_s = elapsed_s
        self.prompt_path = prompt_path
        self.output_path = output_path
        self.last_message_path = last_message_path
        self.prompt_artifact = prompt_artifact
        self.output_artifact = output_artifact
        self.last_message_artifact = last_message_artifact
        self.output_tail = output_tail

    def as_event_details(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "sandbox": self.sandbox,
            "exit_code": self.exit_code,
            "elapsed_s": self.elapsed_s,
            "prompt": self.prompt_artifact,
            "output": self.output_artifact,
            "last_message": self.last_message_artifact,
            "output_tail": self.output_tail,
        }


class RunStore:
    def __init__(
        self,
        repo_root: str | Path,
        run_id: str | None = None,
        runs_dir: str | Path = ".runs",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.run_id = run_id or self._new_run_id()
        runs_path = Path(runs_dir)
        self.runs_root = runs_path if runs_path.is_absolute() else self.repo_root / runs_path
        self.run_dir = self.runs_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"

    def append_event(self, event: str, data: Mapping[str, Any] | None = None) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": dict(data or {}),
        }
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def start(self, task: TaskSpec) -> None:
        self.append_event(
            "run.start",
            {
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "target_path": task.target_path,
                    "instructions": task.instructions,
                    "acceptance_criteria": list(task.acceptance_criteria),
                    "context_paths": list(task.context_paths),
                },
            },
        )

    def finish(self, outcome: LineOutcome) -> None:
        self.append_event("run.finish", outcome.as_event())

    def write_artifact(self, relative_path: str | Path, content: str | bytes) -> Path:
        path = self.artifact_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def write_json_artifact(self, relative_path: str | Path, content: Any) -> Path:
        return self.write_artifact(
            relative_path,
            json.dumps(content, indent=2, sort_keys=True) + "\n",
        )

    def artifact_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact path must stay inside the run directory: {relative_path}")
        return self.run_dir / relative

    def step_dir(self, name: str) -> Path:
        path = self.artifact_path(Path("steps") / name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def check_dir(self, name: str) -> Path:
        path = self.artifact_path(Path("checks") / name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def capture_git_diff(
        self,
        relative_path: str | Path,
        paths: Sequence[str | Path] | None = None,
    ) -> Path:
        path_args = [str(p) for p in paths or ()]
        diff = self._git(["diff", "--binary", "--", *path_args])
        content = diff.stdout
        if diff.stderr:
            content += diff.stderr
        content += self._untracked_file_diffs(path_args)
        artifact = self.write_artifact(relative_path, content)
        self.append_event(
            "run.diff_captured",
            {
                "artifact": self._relative_artifact(artifact),
                "exit_code": diff.returncode,
                "paths": path_args,
            },
        )
        return artifact

    @staticmethod
    def _new_run_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{uuid.uuid4().hex[:8]}"

    def _git(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _untracked_file_diffs(self, path_args: Sequence[str]) -> str:
        listed = self._git(["ls-files", "--others", "--exclude-standard", "--", *path_args])
        if listed.returncode != 0 or not listed.stdout.strip():
            return ""

        chunks: list[str] = []
        for rel in listed.stdout.splitlines():
            diff = subprocess.run(
                ["git", "diff", "--binary", "--no-index", "--", "/dev/null", rel],
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if diff.stdout:
                chunks.append(diff.stdout)
            elif diff.stderr and diff.returncode > 1:
                chunks.append(diff.stderr)
        if not chunks:
            return ""
        return "\n".join(chunks)

    def _relative_artifact(self, path: Path) -> str:
        return str(path.relative_to(self.run_dir))


class ShellCheck:
    def __init__(
        self,
        name: str,
        argv: Sequence[str | Path],
        cwd: str | Path | None = None,
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not argv:
            raise ValueError("argv must contain at least one command element")
        self.name = name
        self.argv = tuple(str(arg) for arg in argv)
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout_s = timeout_s
        self.env = dict(env or {})

    def run(self, ctx: RunContext) -> ShellCheckResult:
        check_dir = ctx.store.check_dir(self.name)
        output_path = check_dir / "output.log"
        result_path = check_dir / "result.json"
        cwd = self._cwd(ctx)
        ctx.store.append_event(
            "check.start",
            {"name": self.name, "argv": list(self.argv), "cwd": str(cwd)},
        )
        output_artifact = ctx.store._relative_artifact(output_path)
        result_artifact = ctx.store._relative_artifact(result_path)
        _terminal_info(
            ctx,
            (
                f"[assemblyline] check.start name={self.name} "
                f"output={output_artifact} result={result_artifact}"
            ),
        )
        _terminal_debug(
            ctx,
            f"[assemblyline] check.debug name={self.name} argv={list(self.argv)!r} cwd={cwd}",
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                list(self.argv),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.timeout_s,
                env=_merged_env(self.env),
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            elapsed = time.monotonic() - start
            details = _exception_event_details(
                name=self.name,
                argv=self.argv,
                cwd=cwd,
                elapsed_s=elapsed,
                exc=exc,
            )
            ctx.store.append_event("check.error", details)
            _terminal_error(
                ctx,
                (
                    f"[assemblyline] check.error name={self.name} "
                    f"exception_type={details['exception_type']} elapsed={elapsed:.3f}s "
                    f"error={details['error']}"
                ),
            )
            raise
        elapsed = time.monotonic() - start
        output = proc.stdout
        exit_code = proc.returncode

        output_path.write_text(output, encoding="utf-8")
        result = {
            "name": self.name,
            "argv": list(self.argv),
            "cwd": str(cwd),
            "exit_code": exit_code,
            "elapsed_s": elapsed,
            "output": ctx.store._relative_artifact(output_path),
        }
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        ctx.store.append_event(
            "check.finish",
            {
                "name": self.name,
                "exit_code": exit_code,
                "elapsed_s": elapsed,
                "output": output_artifact,
                "result": result_artifact,
            },
        )
        status = "pass" if exit_code == 0 else "fail"
        _terminal_info(
            ctx,
            (
                f"[assemblyline] check.finish name={self.name} status={status} "
                f"exit_code={exit_code} elapsed={elapsed:.3f}s "
                f"output={output_artifact} result={result_artifact}"
            ),
        )
        _terminal_debug(
            ctx,
            f"[assemblyline] check.output_tail name={self.name}\n{_bounded_tail(output)}",
        )
        return ShellCheckResult(
            name=self.name,
            argv=self.argv,
            cwd=cwd,
            exit_code=exit_code,
            elapsed_s=elapsed,
            output_path=output_path,
            result_path=result_path,
        )

    def _cwd(self, ctx: RunContext) -> Path:
        if self.cwd is None:
            return ctx.repo_root
        if self.cwd.is_absolute():
            return self.cwd
        return ctx.repo_root / self.cwd


class CodexStep:
    def __init__(
        self,
        name: str,
        sandbox: str,
        build_prompt: Callable[[RunContext, Mapping[str, Any]], str],
        executable: str | Sequence[str] = "codex",
        output_schema: Mapping[str, Any] | str | Path | None = None,
        extra_args: Sequence[str] = (),
        timeout_s: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.sandbox = sandbox
        self.build_prompt = build_prompt
        if isinstance(executable, str):
            self.executable = (executable,)
        else:
            self.executable = tuple(str(part) for part in executable)
        self.output_schema = output_schema
        self.extra_args = tuple(extra_args)
        self.timeout_s = timeout_s
        self.env = dict(env or {})

    def run(
        self,
        ctx: RunContext,
        inputs: Mapping[str, Any] | None = None,
    ) -> CodexStepResult:
        step_dir = ctx.store.step_dir(self.name)
        prompt = self.build_prompt(ctx, inputs or {})
        prompt_path = step_dir / "prompt.md"
        output_path = step_dir / "output.jsonl"
        last_message_path = step_dir / "last_message.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        argv = self._argv(ctx, step_dir, last_message_path)
        prompt_artifact = ctx.store._relative_artifact(prompt_path)
        output_artifact = ctx.store._relative_artifact(output_path)
        last_message_artifact = ctx.store._relative_artifact(last_message_path)

        ctx.store.append_event(
            "step.start",
            {
                "name": self.name,
                "sandbox": self.sandbox,
                "argv": list(argv),
                "prompt": prompt_artifact,
            },
        )
        _terminal_info(
            ctx,
            (
                f"[assemblyline] step.start name={self.name} prompt={prompt_artifact} "
                f"output={output_artifact} last_message={last_message_artifact}"
            ),
        )
        _terminal_debug(
            ctx,
            (
                f"[assemblyline] step.debug name={self.name} argv={list(argv)!r} "
                f"cwd={ctx.repo_root} sandbox={self.sandbox}"
            ),
        )
        _terminal_debug(
            ctx,
            f"[assemblyline] step.prompt name={self.name}\n{_bounded_head(prompt)}",
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                list(argv),
                input=prompt,
                cwd=ctx.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.timeout_s,
                env=_merged_env(self.env),
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            elapsed = time.monotonic() - start
            details = _exception_event_details(
                name=self.name,
                argv=argv,
                cwd=ctx.repo_root,
                elapsed_s=elapsed,
                exc=exc,
                extra={
                    "sandbox": self.sandbox,
                    "prompt": prompt_artifact,
                    "output": output_artifact,
                    "last_message": last_message_artifact,
                },
            )
            ctx.store.append_event("step.error", details)
            _terminal_error(
                ctx,
                (
                    f"[assemblyline] step.error name={self.name} "
                    f"exception_type={details['exception_type']} elapsed={elapsed:.3f}s "
                    f"error={details['error']}"
                ),
            )
            raise
        elapsed = time.monotonic() - start
        output = proc.stdout
        exit_code = proc.returncode

        output_path.write_text(output, encoding="utf-8")
        if exit_code != 0:
            error = CodexStepError(
                name=self.name,
                argv=argv,
                cwd=ctx.repo_root,
                sandbox=self.sandbox,
                exit_code=exit_code,
                elapsed_s=elapsed,
                prompt_path=prompt_path,
                output_path=output_path,
                last_message_path=last_message_path,
                prompt_artifact=prompt_artifact,
                output_artifact=output_artifact,
                last_message_artifact=last_message_artifact,
                output_tail=_bounded_tail(output),
            )
            ctx.store.append_event(
                "step.error",
                {
                    **error.as_event_details(),
                    "exception_type": type(error).__name__,
                    "error": str(error),
                },
            )
            _terminal_error(
                ctx,
                (
                    f"[assemblyline] step.error name={self.name} "
                    f"exit_code={exit_code} elapsed={elapsed:.3f}s "
                    f"output={output_artifact} last_message={last_message_artifact}"
                ),
            )
            _terminal_debug(
                ctx,
                f"[assemblyline] step.output_tail name={self.name}\n{_bounded_tail(output)}",
            )
            raise error

        ctx.store.append_event(
            "step.finish",
            {
                "name": self.name,
                "exit_code": exit_code,
                "elapsed_s": elapsed,
                "output": output_artifact,
                "last_message": last_message_artifact,
            },
        )
        _terminal_info(
            ctx,
            (
                f"[assemblyline] step.finish name={self.name} status=pass "
                f"exit_code={exit_code} elapsed={elapsed:.3f}s "
                f"output={output_artifact} last_message={last_message_artifact}"
            ),
        )
        last_message = (
            last_message_path.read_text(encoding="utf-8") if last_message_path.exists() else ""
        )
        _terminal_debug(
            ctx,
            f"[assemblyline] step.last_message name={self.name}\n{_bounded_tail(last_message)}",
        )
        return CodexStepResult(
            name=self.name,
            argv=argv,
            elapsed_s=elapsed,
            prompt_path=prompt_path,
            output_path=output_path,
            last_message_path=last_message_path,
        )

    def _argv(self, ctx: RunContext, step_dir: Path, last_message_path: Path) -> tuple[str, ...]:
        argv = [
            *self.executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--json",
            "--sandbox",
            self.sandbox,
            "-C",
            str(ctx.repo_root),
            "--output-last-message",
            str(last_message_path),
        ]
        schema_path = self._write_output_schema(step_dir)
        if schema_path is not None:
            argv.extend(["--output-schema", str(schema_path)])
        argv.extend(self.extra_args)
        argv.append("-")
        return tuple(argv)

    def _write_output_schema(self, step_dir: Path) -> Path | None:
        if self.output_schema is None:
            return None
        schema_path = step_dir / "output_schema.json"
        if isinstance(self.output_schema, Mapping):
            schema_path.write_text(
                json.dumps(self.output_schema, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return schema_path
        candidate = Path(self.output_schema)
        if candidate.exists():
            return candidate
        schema_path.write_text(str(self.output_schema), encoding="utf-8")
        return schema_path


def _json_normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _exception_event_details(
    *,
    name: str,
    argv: Sequence[str],
    cwd: Path,
    elapsed_s: float,
    exc: BaseException,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "name": name,
        "argv": list(argv),
        "cwd": str(cwd),
        "elapsed_s": elapsed_s,
        "exception_type": type(exc).__name__,
        "error": str(exc),
    }
    if isinstance(exc, subprocess.TimeoutExpired):
        details["timeout_s"] = exc.timeout
    details.update(dict(extra or {}))
    return details


def _merged_env(overrides: Mapping[str, str]) -> dict[str, str] | None:
    if not overrides:
        return None
    env = os.environ.copy()
    env.update(overrides)
    return env


_TERMINAL_LOG_RANK = {
    TerminalLogLevel.QUIET: 0,
    TerminalLogLevel.INFO: 1,
    TerminalLogLevel.DEBUG: 2,
}
_SNIPPET_LIMIT = 2000


def _terminal_info(ctx: RunContext, message: str) -> None:
    _terminal_log(ctx, TerminalLogLevel.INFO, message)


def _terminal_debug(ctx: RunContext, message: str) -> None:
    _terminal_log(ctx, TerminalLogLevel.DEBUG, message)


def _terminal_error(ctx: RunContext, message: str) -> None:
    _terminal_log(ctx, TerminalLogLevel.INFO, message)


def _terminal_log(ctx: RunContext, level: TerminalLogLevel, message: str) -> None:
    configured = TerminalLogLevel(ctx.terminal_logging)
    if _TERMINAL_LOG_RANK[configured] >= _TERMINAL_LOG_RANK[level]:
        print(message, file=sys.stderr)


def _bounded_head(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    marker = "\n... truncated"
    return text[: max(0, limit - len(marker))] + marker


def _bounded_tail(text: str, limit: int = _SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    marker = "... truncated\n"
    return marker + text[-max(0, limit - len(marker)) :]
