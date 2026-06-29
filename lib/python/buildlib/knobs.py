from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum


@dataclass(frozen=True)
class BuildKnobs:
    """Base for project-specific, typed build knobs.

    Subclass this with project-specific fields (strings, ints, bools, enums, ...).
    ``source_dir`` is required on every knobs object, so a build's source location
    is always an explicit input and is never inferred. ``as_dict()`` flattens every
    field (including subclass fields) to a stable ``dict[str, str]`` for logging,
    run artifacts, and reproducibility.
    """

    source_dir: str

    def as_dict(self) -> dict[str, str]:
        return {f.name: _to_str(getattr(self, f.name)) for f in fields(self)}


def _to_str(value: object) -> str:
    if isinstance(value, bool):  # before int: bool is a subclass of int
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
