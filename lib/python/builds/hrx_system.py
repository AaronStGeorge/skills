from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir

PROJECT = "hrx-system"

# Component installed by the HRX CMake build for downstream consumers (the
# public HRX + loomc distribution: runtime/dev CMake packages and shared libs).
PUBLIC_DIST_COMPONENT = "HrxPublicDist"
TESTS_DIST_COMPONENT = "HrxTestsDist"


@dataclass(frozen=True)
class HrxSystemKnobs(BuildKnobs):
    """Typed knobs for the HRX System build (in addition to the required source_dir).

    HRX is a CMake/Ninja build of the in-tree IREE runtime plus libhrx and the
    Loom tooling. It needs a ROCm/TheRock SDK (for the LLVM/clang toolchain, the
    AMDGPU device toolchain, and ROCm headers); ``rocm_path`` is therefore a
    required, explicit input — like ``source_dir``, it is never inferred.
    """

    rocm_path: str  # ROCm / TheRock SDK root (provides clang, lld, amdgpu toolchain)
    gfx_targets: str = "gfx1151"  # comma/semicolon separated AMDGPU targets
    build_type: str = "RelWithDebInfo"
    jobs: int = 0  # 0 -> let Ninja decide; >0 passes --parallel <jobs>
    loom_build: bool = True  # build the Loom compiler/link tooling (-DLOOM_BUILD=ON)
    install: bool = True  # install the public HRX dist after a successful build
    install_tests: bool = False  # also install the HRX test tree (HrxTestsDist)


@dataclass(frozen=True)
class HrxSystemBuildResult(BuildResult):
    """Result of building the HRX System CMake project (configure + compile [+ install]).

    Testing is the assembly line's responsibility — the build only compiles (and
    optionally installs the public distribution). ``build_exit_code`` is ``None``
    when the build was skipped because configure failed; ``install_exit_code`` is
    ``None`` when install was not requested or was skipped because the build
    failed.
    """

    knobs: HrxSystemKnobs  # narrow the base's knobs field to this project's type
    configure_exit_code: int
    build_exit_code: int | None
    install_exit_code: int | None
    install_path: Path | None
    log: str

    @property
    def built(self) -> bool:
        """True when configure and compile both succeeded.

        Mirrors the workspace rule that builds *compile*; the install of the
        public dist is reported separately via :attr:`installed`.
        """
        return self.configure_exit_code == 0 and self.build_exit_code == 0

    @property
    def installed(self) -> bool:
        """True when the public dist was installed without error."""
        return self.install_exit_code == 0

    def as_prompt_input(self, tail_chars: int = 4000) -> dict[str, object]:
        return {
            "project": self.project,
            "knobs": self.knobs.as_dict(),
            "built": self.built,
            "installed": self.installed,
            "configure_exit_code": self.configure_exit_code,
            "build_exit_code": self.build_exit_code,
            "install_exit_code": self.install_exit_code,
            "build_path": str(self.build_path),
            "install_path": str(self.install_path) if self.install_path else None,
            "log_tail": self.log[-tail_chars:],
        }


def build(knobs: HrxSystemKnobs) -> HrxSystemBuildResult:
    """Configure and build the HRX System CMake project (and optionally install it).

    Output lands in ``<source_dir>/build``; the public dist, when installed, goes
    to ``<source_dir>/build/install``. The ROCm toolchain is taken from
    ``knobs.rocm_path`` and exported into the build environment.
    """
    src = resolve_source_dir(knobs)
    out = build_dir(src)
    out.mkdir(parents=True, exist_ok=True)
    rocm = Path(knobs.rocm_path).expanduser().resolve()
    env = _build_env(rocm, src)

    log_parts: list[str] = []
    configure_rc = _run(_configure_argv(src, out, rocm, knobs), env, log_parts)

    build_rc: int | None = None
    install_rc: int | None = None
    install_path: Path | None = None
    if configure_rc == 0:
        build_argv = ["cmake", "--build", str(out)]
        if knobs.jobs > 0:
            build_argv += ["--parallel", str(knobs.jobs)]
        build_rc = _run(build_argv, env, log_parts)

        if build_rc == 0 and knobs.install:
            install_path = out / "install"
            install_rc = _run(
                _install_argv(out, install_path, PUBLIC_DIST_COMPONENT), env, log_parts
            )
            if install_rc == 0 and knobs.install_tests:
                install_rc = _run(
                    _install_argv(out, out / "install-tests", TESTS_DIST_COMPONENT),
                    env,
                    log_parts,
                )

    return HrxSystemBuildResult(
        project=PROJECT,
        knobs=knobs,
        source_path=src,
        build_path=out,
        configure_exit_code=configure_rc,
        build_exit_code=build_rc,
        install_exit_code=install_rc,
        install_path=install_path,
        log="\n".join(log_parts),
    )


def _configure_argv(src: Path, out: Path, rocm: Path, knobs: HrxSystemKnobs) -> list[str]:
    llvm_bin = rocm / "lib" / "llvm" / "bin"
    argv = [
        "cmake",
        "-S", str(src),
        "-B", str(out),
        "-G", "Ninja",
        f"-DIREE_ROCM_PATH={rocm}",
        "-DIREE_ROCM_DEPENDENCY_MODE=package",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        f"-DCMAKE_C_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_CXX_COMPILER={llvm_bin / 'clang++'}",
        f"-DCMAKE_ASM_COMPILER={llvm_bin / 'clang'}",
        f"-DCMAKE_AR={llvm_bin / 'llvm-ar'}",
        f"-DCMAKE_RANLIB={llvm_bin / 'llvm-ranlib'}",
        "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=lld",
        "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=lld",
        f"-DCMAKE_BUILD_TYPE={knobs.build_type}",
        "-DIREE_HAL_DRIVER_AMDGPU=ON",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DIREE_HAL_DRIVER_VULKAN=ON",
        "-DIREE_HAL_DRIVER_LOCAL_SYNC=ON",
        "-DIREE_HAL_DRIVER_LOCAL_TASK=ON",
        "-DIREE_HAL_DRIVER_NULL=ON",
        f"-DIREE_HAL_AMDGPU_TARGETS={_cmake_targets(knobs.gfx_targets)}",
    ]
    if knobs.loom_build:
        argv.append("-DLOOM_BUILD=ON")
    return argv


def _install_argv(out: Path, prefix: Path, component: str) -> list[str]:
    return [
        "cmake", "--install", str(out),
        "--prefix", str(prefix),
        "--component", component,
    ]


def _cmake_targets(raw: str) -> str:
    """Normalize a comma/semicolon target list to CMake's ``;`` separated form."""
    parts = [p.strip() for p in raw.replace(",", ";").split(";") if p.strip()]
    return ";".join(parts)


def _build_env(rocm: Path, src: Path) -> dict[str, str]:
    """Environment for the HRX build: ROCm toolchain on PATH/LD_LIBRARY_PATH."""
    env = os.environ.copy()
    env["ROCM_PATH"] = str(rocm)
    env["HRX_SYSTEM_SOURCE"] = str(src)
    llvm_bin = rocm / "lib" / "llvm" / "bin"
    env["PATH"] = f"{rocm / 'bin'}:{llvm_bin}:{env.get('PATH', '')}"
    rocm_libs = [rocm / "lib", rocm / "lib64", rocm / "lib" / "rocm_sysdeps" / "lib"]
    env["LD_LIBRARY_PATH"] = ":".join(str(p) for p in rocm_libs) + ":" + env.get("LD_LIBRARY_PATH", "")
    return env


def _run(argv: list[str], env: dict[str, str], log_parts: list[str]) -> int:
    completed = subprocess.run(argv, capture_output=True, text=True, env=env)
    log_parts.append(
        f"$ {' '.join(argv)}\n[exit {completed.returncode}]\n"
        f"{completed.stdout}{completed.stderr}"
    )
    return completed.returncode
