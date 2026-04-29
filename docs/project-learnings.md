# Project Learnings

## Overview

Durable learnings from recent `kurier` work. Keep this file for practical caveats that should survive beyond a single session.

## Stable Learnings

- `src/arkiv/core/llm.py` is the canonical integration point for Ollama, OpenAI-compatible, and Anthropic chat calls. Keep provider logic there instead of adding wrapper dependencies back.
- Pluggy hook calls return lists. When a hook is unimplemented, preserve the original content instead of treating an empty result like a replacement value.
- Memory search quality improves when human-facing fields such as suggested filenames, destination names, and display titles are stored and indexed alongside the core content. A readable `match_reason` also makes retrieval behavior easier to trust and debug.
- A manual review correction is not complete until the item is marked as confirmed. If the category changes without confirming confidence, the entry can fall back into the review queue on the next refresh.

## Workflow Gotchas

- Mocked tests can miss real provider and plugin wiring bugs. After touching classification or routing flow, run at least one smoke test against a real provider.
- `mypy` is strict enough to catch integration details that unit tests may gloss over, especially around subprocess text handling and typed dict shapes.

## Infra / Deploy Notes

- GitHub Actions should use the same editable install path as local development so CI and README do not drift apart.
- The repo-level secret scan is worth treating as permanent CI baseline, not a one-off hardening task. The useful shape is: PR + `main` push + manual dispatch, least-privilege permissions, pinned action revisions, full-history checkout, and no noisy PR comments by default.
- For packaging or CLI changes, a green local dev environment is not enough on its own. Fresh editable-install and wheel-install smoke tests catch first-run problems that normal in-place checks can miss.
