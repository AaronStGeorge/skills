# ws-template

> The trouble with having an open mind, of course, is that people will insist on coming along and trying to put things in it.
>
>   –Terry Pratchett

This repo holds a template for agent workspace. Clone with `git clone git@github.com:AaronStGeorge/ws-template.git <project>-ws`, and clone what you're working on into `sources`. Then you're off to the races! Except rather than horses, agents are zipping around — consuming tokens, farting carbon, and hopefully doing something useful.

## Layout

```
lib/python/            # shared library (editable-installed)
  assemblyline/        # loop primitives (steps, checks, run store, outcomes)
  buildlib/            # typed build bases: BuildKnobs, BuildResult, + path helpers
  builds/              # one file per project; each exposes build(knobs, *deps) -> *BuildResult
  tests/               # library regression tests
skills/                # agent skills (e.g. assemblyline)
sources/               # cloned target repos; each builds into sources/<repo>/build
```

## Design

The library is a single editable install exposing three independent packages, so any consumer —
an assembly-line script, a standalone single-file build skill, anything — reaches it with plain
`import`s and no path bootstrap:

- **`assemblyline`** — primitives for code-owned Codex loops: `CodexStep`, `ShellCheck`,
  `RunStore` / `RunContext`, `TaskSpec`, `LineOutcome`. (Authoring guide:
  `skills/assemblyline/SKILL.md`; API: `skills/assemblyline/references/library-api.md`.)
- **`buildlib`** — the build contract, as two frozen-dataclass **bases** (no Protocols: every knobs
  and result type is one you author and subclass). `BuildKnobs` carries a required `source_dir` — so
  a build's source is **always an explicit input, never inferred** — plus an `as_dict()` that flattens
  typed fields to `str` for logging/reproducibility; projects subclass it with **typed** fields
  (e.g. `ToyMlKnobs(build_type=..., jobs=...)`). `BuildResult` carries `project`, the `knobs`, and the
  source/build paths; projects subclass it too. `build_dir(src)` resolves `<src>/build`.
- **`builds`** — one module per project. Each is a `build(knobs, *deps) -> <X>BuildResult` function in
  its own file — a project's typed `XKnobs` in, its typed `XBuildResult` out. A build that depends on
  another project being built a certain way takes that project's `XBuildResult` as an argument. Output
  always lands in `<source_dir>/build`, so builds behave the same for a worktree or a clone in
  `sources/`.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e lib/python --config-settings editable_mode=compat
```

That's it — `import assemblyline`, `from buildlib import ...`, and `from builds.toy_ml import build`
now work from anywhere, including from inside a target repo or worktree.

`editable_mode=compat` makes the install a plain path entry rather than a PEP 660 import
hook, so static analyzers (Pylance/pyright in VS Code) resolve the packages too — not just the
runtime.

Quick check:

```bash
python -m unittest discover -s lib/python/tests   # library regression tests
python -c "from builds.toy_ml import build, ToyMlKnobs; r = build(ToyMlKnobs(source_dir='skills/assemblyline/examples/toy-tasks/toy-ml')); print('built:', r.built, '-> build at', r.build_path)"
```

## Example: the ReLU assembly line

[`skills/assemblyline/examples/relu_line.py`](skills/assemblyline/examples/relu_line.py) is a
complete, runnable assembly line. It solves the intentionally-broken C++ ReLU in
`skills/assemblyline/examples/toy-tasks/toy-ml` by:

1. building the toy project with `builds.toy_ml.build`, then running `ctest` **in the line** (baseline — tests fail),
2. running a Codex *maker* step to implement `toy::relu`,
3. rebuilding via the **same** `builds.toy_ml.build` and re-testing in the line (now passing),
4. running a Codex *review* step and emitting an approved / rejected / failed `LineOutcome`.

The build library only **builds**; the line **tests** (so test runs are logged as line `ShellCheck` artifacts). That's the split that lets a plain build script reuse `builds.toy_ml.build` without inheriting any test policy.


```bash
source .venv/bin/activate
python skills/assemblyline/examples/relu_line.py
```

Run artifacts — prompts, raw Codex JSONL, captured diffs, and decisions — are written under the
run store (`.runs/<run-id>/`).
