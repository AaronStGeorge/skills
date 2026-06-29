from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from buildlib import BuildKnobs, BuildResult, build_dir, resolve_source_dir
from builds.toy_ml import ToyMlBuildResult, ToyMlKnobs


class BuildKnobsTests(unittest.TestCase):
    def test_source_dir_is_required(self) -> None:
        with self.assertRaises(TypeError):
            ToyMlKnobs()  # type: ignore[call-arg]

    def test_subclass_is_a_buildknobs(self) -> None:
        self.assertIsInstance(ToyMlKnobs(source_dir="/x/toy"), BuildKnobs)

    def test_as_dict_stringifies_typed_fields(self) -> None:
        knobs = ToyMlKnobs(source_dir="/x/toy", build_type="Release", jobs=4)
        self.assertEqual(
            knobs.as_dict(),
            {"source_dir": "/x/toy", "build_type": "Release", "jobs": "4"},
        )

    def test_as_dict_handles_bool_and_enum(self) -> None:
        class Mode(Enum):
            FAST = "fast"

        @dataclass(frozen=True)
        class K(BuildKnobs):
            flag: bool = True
            mode: Mode = Mode.FAST

        self.assertEqual(
            K(source_dir="/x").as_dict(),
            {"source_dir": "/x", "flag": "true", "mode": "fast"},
        )


class PathHelperTests(unittest.TestCase):
    def test_build_dir(self) -> None:
        self.assertEqual(build_dir(Path("/x/toy")), Path("/x/toy/build"))

    def test_resolve_source_dir_is_absolute(self) -> None:
        knobs = ToyMlKnobs(source_dir="skills/assemblyline/examples/toy-tasks/toy-ml")
        resolved = resolve_source_dir(knobs)
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "toy-ml")


class BuildResultTests(unittest.TestCase):
    def test_subclasses_base_and_carries_knobs(self) -> None:
        knobs = ToyMlKnobs(source_dir="/x/toy")
        result = ToyMlBuildResult(
            project="toy-ml",
            knobs=knobs,
            source_path=Path("/x/toy"),
            build_path=Path("/x/toy/build"),
            configure_exit_code=0,
            build_exit_code=0,
            log="",
        )
        self.assertIsInstance(result, BuildResult)
        self.assertIs(result.knobs, knobs)
        self.assertTrue(result.built)


if __name__ == "__main__":
    unittest.main()
