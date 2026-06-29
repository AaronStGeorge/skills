from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .knobs import BuildKnobs


@dataclass(frozen=True)
class BuildResult:
    """Common frozen base for concrete ``*BuildResult`` dataclasses.

    Subclass and append project-specific fields (and typically narrow ``knobs`` to
    the project's knobs type). ``knobs`` holds the immutable knobs the build ran
    with, so the result fully describes where it came from.
    """

    project: str
    knobs: BuildKnobs
    source_path: Path
    build_path: Path
