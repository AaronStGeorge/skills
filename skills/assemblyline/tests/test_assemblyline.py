from __future__ import annotations

import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib" / "python"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import assemblyline
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


def make_context(
    root: Path,
    terminal_logging: TerminalLogLevel = TerminalLogLevel.QUIET,
) -> RunContext:
    task = TaskSpec(
        id="task",
        title="Task",
        target_path=".",
        instructions="Do the thing.",
        acceptance_criteria=["It works."],
    )
    store = RunStore(root, run_id="run-test")
    return RunContext(
        task=task,
        repo_root=root,
        run_id=store.run_id,
        store=store,
        terminal_logging=terminal_logging,
    )


def read_events(store: RunStore) -> list[dict[str, object]]:
    return [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]


class PublicApiTests(unittest.TestCase):
    def test_exports_current_core_api_without_legacy_aliases(self) -> None:
        for name in (
            "CodexStep",
            "CodexStepError",
            "CodexStepResult",
            "LineOutcome",
            "RunContext",
            "RunStore",
            "ShellCheck",
            "ShellCheckResult",
            "TaskSpec",
            "TerminalLogLevel",
        ):
            self.assertIn(name, assemblyline.__all__)
            self.assertTrue(hasattr(assemblyline, name))

        for legacy_name in ("CheckResult", "StepResult", "CheckerDecision"):
            self.assertNotIn(legacy_name, assemblyline.__all__)
            self.assertFalse(hasattr(assemblyline, legacy_name))


class TaskSpecTests(unittest.TestCase):
    def test_prompt_input(self) -> None:
        task = TaskSpec(
            id="task",
            title="Task",
            target_path="src",
            instructions="Do it.",
            acceptance_criteria=["It works.", "It is focused."],
            context_paths=["src/main.py"],
        )

        self.assertIn("ID: task", task.as_prompt_input())
        self.assertIn("- It works.", task.as_prompt_input())
        self.assertIn("Context paths:\n- src/main.py", task.as_prompt_input())


class LineOutcomeTests(unittest.TestCase):
    def test_constructors_properties_and_event_shape(self) -> None:
        approved = LineOutcome.approved(
            reason="review_approved",
            message="ok",
            details={"path": Path("src/relu.cpp")},
        )
        rejected = LineOutcome.rejected(reason="review_rejected", message="no")
        failed = LineOutcome.failed(reason="maker_failed", message="boom")

        self.assertTrue(approved.ok)
        self.assertEqual(approved.exit_code, 0)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.exit_code, 1)
        self.assertFalse(failed.ok)
        self.assertEqual(failed.exit_code, 1)
        self.assertEqual(
            approved.as_event(),
            {
                "status": "approved",
                "reason": "review_approved",
                "message": "ok",
                "details": {"path": "src/relu.cpp"},
            },
        )

    def test_invalid_status_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LineOutcome("maybe", "reason", "message")

    def test_failed_exception_merges_exception_diagnostics(self) -> None:
        class EventDetailsError(RuntimeError):
            def as_event_details(self) -> dict[str, object]:
                return {
                    "exit_code": 9,
                    "output": "steps/review/output.jsonl",
                    "note": "from-event-details",
                }

        try:
            raise EventDetailsError("boom")
        except EventDetailsError as exc:
            outcome = LineOutcome.failed_exception(
                exc,
                details={"note": "from-caller", "path": Path("build/out")},
            )

        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.reason, "unexpected_exception")
        self.assertEqual(outcome.details["note"], "from-event-details")
        self.assertEqual(outcome.details["exit_code"], 9)
        self.assertEqual(outcome.details["output"], "steps/review/output.jsonl")
        self.assertEqual(outcome.details["exception_type"], "EventDetailsError")
        self.assertEqual(outcome.details["error"], "boom")
        self.assertIn("EventDetailsError: boom", outcome.details["traceback"])
        self.assertEqual(outcome.as_event()["details"]["path"], "build/out")


class TerminalLogLevelTests(unittest.TestCase):
    def test_parse_values_used_by_template(self) -> None:
        self.assertEqual(TerminalLogLevel("quiet"), TerminalLogLevel.QUIET)
        self.assertEqual(TerminalLogLevel("info"), TerminalLogLevel.INFO)
        self.assertEqual(TerminalLogLevel(" debug ".strip().lower()), TerminalLogLevel.DEBUG)

        with self.assertRaises(ValueError):
            TerminalLogLevel("verbose")

    def test_run_context_requires_explicit_logging_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = TaskSpec("task", "Task", ".", "Do it.", ["It works."])
            store = RunStore(Path(tmp), run_id="abc")
            ctx = RunContext(
                task=task,
                repo_root=Path(tmp),
                run_id=store.run_id,
                store=store,
                terminal_logging=TerminalLogLevel.INFO,
            )

            self.assertEqual(ctx.terminal_logging, TerminalLogLevel.INFO)


class RunStoreTests(unittest.TestCase):
    def test_event_and_artifact_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = RunStore(root, run_id="abc")

            artifact = store.write_artifact("nested/output.txt", "hello\n")
            store.append_event("run.start", {"artifact": "nested/output.txt"})

            self.assertEqual(artifact.read_text(encoding="utf-8"), "hello\n")
            events = store.events_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0])
            self.assertEqual(event["run_id"], "abc")
            self.assertEqual(event["event"], "run.start")
            self.assertEqual(event["data"]["artifact"], "nested/output.txt")

    def test_artifact_paths_cannot_escape_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp), run_id="abc")
            with self.assertRaises(ValueError):
                store.write_artifact("../outside.txt", "nope")

    def test_start_writes_stable_run_start_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp), run_id="abc")
            task = TaskSpec("task", "Task", ".", "Do it.", ["It works."])

            store.start(task)

            events = store.events_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0])
            self.assertEqual(event["event"], "run.start")
            self.assertEqual(
                event["data"],
                {
                    "task": {
                        "id": "task",
                        "title": "Task",
                        "target_path": ".",
                        "instructions": "Do it.",
                        "acceptance_criteria": ["It works."],
                        "context_paths": [],
                    },
                },
            )

    def test_finish_writes_stable_run_finish_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp), run_id="abc")
            outcome = LineOutcome.failed(
                reason="unexpected_exception",
                message="failed",
                details={"path": Path("build/out")},
            )

            store.finish(outcome)

            events = store.events_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 1)
            event = json.loads(events[0])
            self.assertEqual(event["event"], "run.finish")
            self.assertEqual(
                event["data"],
                {
                    "status": "failed",
                    "reason": "unexpected_exception",
                    "message": "failed",
                    "details": {"path": "build/out"},
                },
            )


class ShellCheckTests(unittest.TestCase):
    def test_shell_check_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_context(Path(tmp))

            result = ShellCheck(
                "pass",
                [sys.executable, "-c", "print('ok')"],
            ).run(ctx)

            self.assertIsInstance(result, ShellCheckResult)
            self.assertTrue(result.ok)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.output, "ok\n")
            self.assertEqual(
                result.as_prompt_input(),
                {"exit_code": 0, "ok": True, "tail": "ok\n"},
            )
            self.assertFalse(hasattr(result, "timed_out"))

    def test_shell_check_completed_nonzero_returns_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_context(Path(tmp))

            result = ShellCheck(
                "fail",
                [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(3)",
                ],
            ).run(ctx)

            self.assertIsInstance(result, ShellCheckResult)
            self.assertFalse(result.ok)
            self.assertEqual(result.exit_code, 3)
            self.assertIn("out", result.output)
            self.assertIn("err", result.output)
            self.assertNotIn("timed_out", json.loads(result.result_path.read_text()))
            self.assertEqual(
                [event["event"] for event in read_events(ctx.store)],
                ["check.start", "check.finish"],
            )

    def test_missing_executable_raises_and_writes_error_event_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_context(Path(tmp))

            with self.assertRaises(FileNotFoundError):
                ShellCheck(
                    "missing",
                    ["assemblyline-missing-executable-for-test"],
                ).run(ctx)

            events = read_events(ctx.store)
            self.assertEqual([event["event"] for event in events], ["check.start", "check.error"])
            self.assertEqual(events[1]["data"]["exception_type"], "FileNotFoundError")
            check_dir = ctx.store.artifact_path("checks/missing")
            self.assertFalse((check_dir / "output.log").exists())
            self.assertFalse((check_dir / "result.json").exists())

    def test_timeout_raises_and_writes_error_event_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_context(Path(tmp))

            with self.assertRaises(subprocess.TimeoutExpired):
                ShellCheck(
                    "timeout",
                    [sys.executable, "-c", "import time; time.sleep(1)"],
                    timeout_s=0.05,
                ).run(ctx)

            events = read_events(ctx.store)
            self.assertEqual([event["event"] for event in events], ["check.start", "check.error"])
            self.assertEqual(events[1]["data"]["exception_type"], "TimeoutExpired")
            self.assertEqual(events[1]["data"]["timeout_s"], 0.05)
            check_dir = ctx.store.artifact_path("checks/timeout")
            self.assertFalse((check_dir / "output.log").exists())
            self.assertFalse((check_dir / "result.json").exists())


class TerminalLoggingTests(unittest.TestCase):
    def test_shell_check_quiet_info_and_debug_logging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            quiet_ctx = make_context(Path(tmp) / "quiet", TerminalLogLevel.QUIET)
            quiet_log = io.StringIO()
            with redirect_stderr(quiet_log):
                ShellCheck("quiet", [sys.executable, "-c", "print('quiet output')"]).run(quiet_ctx)
            self.assertEqual(quiet_log.getvalue(), "")

        with tempfile.TemporaryDirectory() as tmp:
            info_ctx = make_context(Path(tmp) / "info", TerminalLogLevel.INFO)
            info_log = io.StringIO()
            with redirect_stderr(info_log):
                ShellCheck("info", [sys.executable, "-c", "print('command body')"]).run(info_ctx)
            info = info_log.getvalue()
            self.assertIn("check.start name=info", info)
            self.assertIn("check.finish name=info status=pass exit_code=0", info)
            self.assertIn("output=checks/info/output.log", info)
            self.assertNotIn("check.debug", info)
            self.assertNotIn("command body", info)

        with tempfile.TemporaryDirectory() as tmp:
            debug_ctx = make_context(Path(tmp) / "debug", TerminalLogLevel.DEBUG)
            debug_log = io.StringIO()
            with redirect_stderr(debug_log):
                ShellCheck("debug", [sys.executable, "-c", "print('debug output')"]).run(debug_ctx)
            debug = debug_log.getvalue()
            self.assertIn("check.start name=debug", debug)
            self.assertIn("check.debug name=debug", debug)
            self.assertIn("argv=", debug)
            self.assertIn("cwd=", debug)
            self.assertIn("check.output_tail name=debug", debug)
            self.assertIn("debug output", debug)

    def test_codex_step_debug_logs_prompt_and_last_message_not_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = make_context(root, TerminalLogLevel.DEBUG)
            fake = root / "fake_codex.py"
            fake.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json",
                        "import pathlib",
                        "import sys",
                        "args = sys.argv[1:]",
                        "sys.stdin.read()",
                        "last = pathlib.Path(args[args.index('--output-last-message') + 1])",
                        "last.write_text('review final message', encoding='utf-8')",
                        "print(json.dumps({'type': 'raw-jsonl-only'}))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            step = CodexStep(
                "review",
                "read-only",
                lambda _ctx, _inputs: "prompt body",
                executable=str(fake),
            )

            log = io.StringIO()
            with redirect_stderr(log):
                step.run(ctx)

            stderr = log.getvalue()
            self.assertIn("step.start name=review", stderr)
            self.assertIn("step.debug name=review", stderr)
            self.assertIn("sandbox=read-only", stderr)
            self.assertIn("step.prompt name=review", stderr)
            self.assertIn("prompt body", stderr)
            self.assertIn("step.finish name=review status=pass exit_code=0", stderr)
            self.assertIn("step.last_message name=review", stderr)
            self.assertIn("review final message", stderr)
            self.assertNotIn("raw-jsonl-only", stderr)


class CodexStepTests(unittest.TestCase):
    def test_codex_step_success_constructs_command_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = make_context(root)
            fake = root / "fake_codex.py"
            record = root / "record.json"
            fake.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json",
                        "import pathlib",
                        "import sys",
                        f"record = pathlib.Path({str(record)!r})",
                        "args = sys.argv[1:]",
                        "prompt = sys.stdin.read()",
                        "last = pathlib.Path(args[args.index('--output-last-message') + 1])",
                        "last.write_text('step final message', encoding='utf-8')",
                        "record.write_text(json.dumps({'argv': args, 'prompt': prompt}), encoding='utf-8')",
                        "print(json.dumps({'type': 'done'}))",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            step = CodexStep(
                "maker",
                "workspace-write",
                lambda _ctx, inputs: f"prompt {inputs['value']}",
                executable=str(fake),
            )
            result = step.run(ctx, {"value": 42})

            self.assertIsInstance(result, CodexStepResult)
            self.assertFalse(hasattr(result, "ok"))
            self.assertFalse(hasattr(result, "exit_code"))
            self.assertEqual(result.last_message, "step final message")
            written = json.loads(record.read_text(encoding="utf-8"))
            expected_prefix = [
                "--ask-for-approval",
                "never",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "-C",
                str(root.resolve()),
                "--output-last-message",
            ]
            self.assertEqual(written["argv"][: len(expected_prefix)], expected_prefix)
            self.assertTrue(written["argv"][len(expected_prefix)].endswith("last_message.txt"))
            self.assertEqual(written["argv"][-1], "-")
            self.assertEqual(written["prompt"], "prompt 42")
            self.assertEqual(result.prompt_path.read_text(encoding="utf-8"), "prompt 42")
            self.assertEqual(result.prompt_path.parent.parent.name, "steps")
            self.assertEqual(result.prompt_path.relative_to(ctx.store.run_dir).parts[:2], ("steps", "maker"))
            events = read_events(ctx.store)
            self.assertEqual([event["event"] for event in events], ["step.start", "step.finish"])
            self.assertEqual(events[0]["data"]["prompt"], "steps/maker/prompt.md")
            self.assertEqual(events[1]["data"]["exit_code"], 0)

    def test_codex_step_writes_output_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = make_context(root)
            fake = root / "fake_codex.py"
            record = root / "record.json"
            fake.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json",
                        "import pathlib",
                        "import sys",
                        f"record = pathlib.Path({str(record)!r})",
                        "args = sys.argv[1:]",
                        "pathlib.Path(args[args.index('--output-last-message') + 1]).write_text('{}', encoding='utf-8')",
                        "record.write_text(json.dumps({'argv': args}), encoding='utf-8')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            step = CodexStep(
                "review",
                "read-only",
                lambda _ctx, _inputs: "check",
                executable=str(fake),
                output_schema={"type": "object"},
            )
            result = step.run(ctx)

            self.assertIsInstance(result, CodexStepResult)
            argv = json.loads(record.read_text(encoding="utf-8"))["argv"]
            self.assertIn("--output-schema", argv)
            schema_path = Path(argv[argv.index("--output-schema") + 1])
            self.assertEqual(schema_path.parent.parent.name, "steps")
            self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), {"type": "object"})

    def test_codex_step_nonzero_raises_error_and_writes_error_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = make_context(root)
            fake = root / "fake_codex.py"
            fake.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import pathlib",
                        "import sys",
                        "args = sys.argv[1:]",
                        "sys.stdin.read()",
                        "last = pathlib.Path(args[args.index('--output-last-message') + 1])",
                        "last.write_text('failure final message', encoding='utf-8')",
                        "print('codex failed output')",
                        "raise SystemExit(7)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            step = CodexStep(
                "review",
                "read-only",
                lambda _ctx, _inputs: "prompt body",
                executable=str(fake),
            )

            with self.assertRaises(CodexStepError) as raised:
                step.run(ctx)

            error = raised.exception
            self.assertEqual(error.exit_code, 7)
            self.assertEqual(error.sandbox, "read-only")
            self.assertIn("codex failed output", error.output_tail)
            self.assertTrue(error.output_path.exists())
            self.assertEqual(error.output_path.read_text(encoding="utf-8"), "codex failed output\n")
            self.assertEqual(error.last_message_path.read_text(encoding="utf-8"), "failure final message")
            details = error.as_event_details()
            self.assertEqual(details["prompt"], "steps/review/prompt.md")
            self.assertEqual(details["output"], "steps/review/output.jsonl")
            self.assertEqual(details["last_message"], "steps/review/last_message.txt")
            self.assertEqual(details["exit_code"], 7)
            self.assertEqual(details["sandbox"], "read-only")
            self.assertEqual(details["cwd"], str(root))
            self.assertIn("argv", details)

            events = read_events(ctx.store)
            self.assertEqual([event["event"] for event in events], ["step.start", "step.error"])
            self.assertEqual(events[1]["data"]["exit_code"], 7)
            self.assertEqual(events[1]["data"]["output"], "steps/review/output.jsonl")
            self.assertIn("codex failed output", events[1]["data"]["output_tail"])


if __name__ == "__main__":
    unittest.main()
