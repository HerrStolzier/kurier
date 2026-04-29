# AGENTS.md

## Documentation Model

- Keep stable rules and workflow defaults in this file.
- Put session-derived caveats in `docs/project-learnings.md`.
- Treat `CLAUDE.md` as a compatibility layer, not the only source of truth.

## Stable Rules

- Route all LLM calls through `src/arkiv/core/llm.py`. Do not reintroduce `litellm`.
- Keep the external package name `kurier`, but the internal Python package path remains `arkiv`.
- Keep `.github/workflows/secret-scan.yml` as a repo baseline: run it on pull requests, `main` pushes, and manual dispatch, with `contents: read`, pinned action revisions, and `fetch-depth: 0`.
- After code changes, run `ruff check src/`, `mypy src/arkiv/ --ignore-missing-imports`, and `pytest tests/ -x -q`. If changes touch `plugins/arkiv-webhook/`, also run `pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/`.
- If classification, provider wiring, or plugin hooks change, verify at least one real-provider smoke path instead of relying only on mocked unit tests.
- If packaging, install flow, or CLI entrypoints change, smoke-test both a fresh editable install and a fresh wheel install instead of trusting only the in-place dev environment.
