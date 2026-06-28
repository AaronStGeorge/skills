from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from assemblyline import CodexStep, RunContext


@dataclass(frozen=True)
class ReviewResult:
    review_ok: bool
    summary: str
    issues: tuple[str, ...]
    required_fixes: tuple[str, ...]

    @classmethod
    def from_json_text(cls, text: str) -> "ReviewResult":
        obj = _load_json_object(text)
        approved = obj.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("review output field 'approved' must be a boolean")

        return cls(
            review_ok=approved,
            summary=_require_string(obj, "summary"),
            issues=_require_string_tuple(obj, "issues"),
            required_fixes=_require_string_tuple(obj, "required_fixes"),
        )

    def as_prompt_input(self) -> dict[str, Any]:
        return {
            "review_ok": self.review_ok,
            "summary": self.summary,
            "issues": list(self.issues),
            "required_fixes": list(self.required_fixes),
        }


class Review:
    OUTPUT_SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "approved": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "string"}},
            "required_fixes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["approved", "summary", "issues", "required_fixes"],
    }

    def __init__(
        self,
        name: str,
        build_prompt: Callable[[RunContext, Mapping[str, Any]], str],
    ) -> None:
        self.step = CodexStep(
            name,
            "read-only",
            build_prompt,
            output_schema=self.OUTPUT_SCHEMA,
        )

    def run(
        self,
        ctx: RunContext,
        inputs: Mapping[str, Any] | None = None,
    ) -> ReviewResult:
        result = self.step.run(ctx, inputs or {})
        return ReviewResult.from_json_text(result.last_message)


def _load_json_object(text: str) -> dict[str, Any]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"review output must be valid JSON: {exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("review output must be a JSON object")
    return loaded


def _require_string(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ValueError(f"review output field {key!r} must be a string")
    return value


def _require_string_tuple(obj: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = obj.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(f"review output field {key!r} must be an array of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"review output field {key!r} must contain only strings")
    return tuple(value)
