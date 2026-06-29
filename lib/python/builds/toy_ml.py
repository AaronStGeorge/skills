from __future__ import annotations

import subprocess
from dataclasses import dataclass

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir

PROJECT = "toy-ml"


@dataclass(frozen=True)
class ToyMlKnobs(BuildKnobs):
    """Typed knobs for the toy-ml build (in addition to the required source_dir)."""

    build_type: str = "Debug"
    jobs: int = 0  # 0 -> let CMake decide; >0 passes --parallel <jobs>


@dataclass(frozen=True)
class ToyMlBuildResult(BuildResult):
    """Result of building the toy-ml CMake project (configure + compile only).

    Testing is the assembly line's responsibility — run ``ctest`` against
    ``build_path``. ``build_exit_code`` is ``None`` when the build was skipped
    because configure failed.
    """

    knobs: ToyMlKnobs  # narrow the base's knobs field to this project's type
    configure_exit_code: int
    build_exit_code: int | None
    log: str

    @property
    def built(self) -> bool:
        return self.configure_exit_code == 0 and self.build_exit_code == 0

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, object]:
        return {
            "project": self.project,
            "knobs": self.knobs.as_dict(),
            "built": self.built,
            "configure_exit_code": self.configure_exit_code,
            "build_exit_code": self.build_exit_code,
            "build_path": str(self.build_path),
            "log_tail": self.log[-tail_chars:],
        }


def build(knobs: ToyMlKnobs) -> ToyMlBuildResult:
    """Configure and build the toy-ml CMake project."""
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)

    log_parts: list[str] = []
    configure_rc = _run(
        ["cmake", "-S", str(src), "-B", str(out), f"-DCMAKE_BUILD_TYPE={knobs.build_type}"],
        log_parts,
    )

    build_rc: int | None = None
    if configure_rc == 0:
        argv = ["cmake", "--build", str(out)]
        if knobs.jobs > 0:
            argv += ["--parallel", str(knobs.jobs)]
        build_rc = _run(argv, log_parts)

    return ToyMlBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        log="\n".join(log_parts),
    )


def _run(argv: list[str], log_parts: list[str]) -> int:
    completed = subprocess.run(argv, capture_output=True, text=True)
    log_parts.append(
        f"$ {' '.join(argv)}\n[exit {completed.returncode}]\n"
        f"{completed.stdout}{completed.stderr}"
    )
    return completed.returncode
