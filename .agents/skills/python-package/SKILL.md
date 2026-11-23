---
name: python-package
description: Scaffold or repair a typed Python package (src layout, explicit public API, py.typed, build metadata, a committed lockfile, entry points) and prove it from a wheel installed outside the checkout. Use for new packages and packaging failures; not for application design or for publishing a release (release).
---

# Python package

A package is done when a fresh environment can install its wheel and use it, not when it
imports from the checkout.

## Steps

1. **Read what exists:** the project's instructions, `pyproject.toml`, the build backend
   and the lockfile tool. Keep the project's tooling; do not switch it.
2. **Name things once:** the distribution name, the import name and the supported Python
   versions. Keep the package version apart from any schema or data version.
3. **Layout:** `src/<package>/` with an explicit public API, a `py.typed` marker, type hints
   on every function and docstrings on every source function. Strict type checking, and
   never suppress an error: fix its cause.
4. **Dependencies:** runtime dependencies only for what the core imports; optional extras
   for anything else (cloud SDKs, vendor clients). Commit the lockfile, and check it is
   current in CI (for example `uv lock --check`).
5. **Data files:** templates and other package data live inside the package and are read
   with `importlib.resources`, never by a path relative to the checkout.
6. **Entry points** only for commands that exist, each with `--help`.
7. **Prove it:** build the wheel (and the sdist, then a wheel from the sdist), install it in
   a fresh environment outside the checkout, and run the imports, the data reads and every
   advertised command. Check the core imports without the optional extras installed.
8. **Report** what was built, which environments ran it and what each check returned.

## When not to use

- Publishing to an index or tagging a version: use `release`, and only when the owner asks.
- Never invent dependency pins or hashes; let the lockfile tool resolve them.
