"""Per-project build functions.

Each module here owns exactly one project's build: a ``build(knobs, *deps)``
function that takes the project's ``BuildKnobs`` subclass (and any upstream
``*BuildResult`` it depends on) and returns its own ``buildlib.BuildResult`` subclass.
"""
