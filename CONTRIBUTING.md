# Contributing to BioHarbor

Thanks for helping! Bug reports, new tools and docs are all welcome.

## Development setup

```bash
git clone https://github.com/danielLuo2/bioharbor && cd bioharbor
uv venv && uv pip install -e ".[dev]"     # or: python -m venv .venv && pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

Tests must not need a GPU or network: inject fake GPUs with `Harbor(gpu_probe=...)`
(see `tests/test_jobs.py`).

## Adding a tool

1. Create `src/bioharbor/tools/<area>.py` (or a separate package for plugins).
2. Define a pydantic params model; every field needs a `description` — that is what the
   agent reads.
3. Decorate the function with `@tool(...)`:
   - `slow=True` if it can take more than a few seconds (runs as a background job);
   - `resources=Resources(gpu=True, gpu_mem_gb=...)` if it needs a GPU — make the memory
     estimate depend on the input size where possible.
4. Validate input early and raise `InputValidationError(msg, hint=...)` with a hint the
   agent can act on.
5. Return a `ToolResult`: a **compact** `summary`, a one-line `message`, big outputs written
   to `ctx.workdir` and listed in `files`, and optional `suggestions`.
6. Add it to `BUILTIN_MODULES` in `registry.py`, write tests, update the README tool table
   and `CHANGELOG.md`.

Plugins in other packages register via entry points:

```toml
[project.entry-points."bioharbor.tools"]
my_tools = "my_package.tools"
```

## Pull requests

- Keep PRs focused; include tests.
- Never commit tokens, server addresses or private data.
