from __future__ import annotations

from pathlib import Path

from .knobs import BuildKnobs


def resolve_source_dir(knobs: BuildKnobs) -> Path:
    """Absolute source directory for a build, from the required ``source_dir`` field.

    The source directory is always an explicit input on the knobs; it is never
    inferred from a sources root, project name, or the current working directory.
    """
    return Path(knobs.source_dir).expanduser().resolve()


def build_dir(source_dir: Path) -> Path:
    """The build output directory for a source checkout: ``<source_dir>/build``."""
    return Path(source_dir) / "build"
