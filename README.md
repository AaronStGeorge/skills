# ws-template

> The trouble with having an open mind, of course, is that people will insist on coming along and trying to put things in it.
>
>   –Terry Pratchett

This repo holds an agent workspace template. Clone `git clone git@github.com:AaronStGeorge/ws-template.git <project>-ws`, then clone what you're working on into `sources`, and you're off to the races! Except rather than horses, agents are zipping around — consuming tokens, farting carbon, and hopefully doing something useful.

This workspace is designed around a containment strategy. I build chutes and gates so my little horde of mercurial genius-idiots has to run in vaguely the right direction. On the ground, that looks like `assemblyline` scripts and lots of specially built tools. Tools for agents are intended to be repeatable, consistent, button presses.

My agents' little hammers built in Python. Not for any particular reason. Most things would probably work. Maybe not Tcl. I know Python and it's now slopped out at your local agent farm for a few cents a pound, so... sure.

## Setup

`direnv` will do it all for you, alternatively:
```bash
python3 -m venv .venv --prompt ${PWD##*/} && source .venv/bin/activate
pip install -e lib/python --config-settings editable_mode=compat
```

`editable_mode=compat` makes the install a plain path entry rather than a PEP
660 import hook, static analyzers (Pylance/pyright in VS Code) have an issue
without that for some unknown reason.

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
