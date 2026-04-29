# Product Maturity Snapshot

Last updated: **2026-04-10**

This document is meant to answer one practical question:

> What can a new user trust today, what works but still needs caution, and what is intentionally not part of the current core product?

## How to read the labels

- **Stable** means the flow has already been exercised in a realistic end-to-end way and is a fair part of the current core promise.
- **Usable** means the feature is real and helpful, but still has meaningful quality variance, edge cases, or thinner validation than the core flow.
- **Experimental** means the feature exists and may already be useful, but it should not yet be treated as a fully proven product path.
- **Deferred** means the idea is known and still reasonable, but it is intentionally not part of the current delivery focus.

## Matrix

| Area | Status | Why this rating is fair right now |
|------|--------|------------------------------------|
| Install and first-run setup | **Stable** | Fresh developer installs and wheel installs were both re-tested from zero. `kurier init` is in place and `kurier doctor --fix` now smooths out missing-directory setup friction. |
| Core CLI intake and routing | **Stable** | `kurier add`, `kurier watch`, `kurier undo`, and the standard folder-routing path have been exercised as real product flows, not just mocked unit tests. |
| Dashboard review flow | **Stable** | The dashboard static asset bug was fixed and the manual review regression was fixed so confirmed corrections no longer reappear after refresh. |
| API server | **Stable** | `kurier serve` and the main API surface are part of the verified product path and support the same local-first workflow as the CLI. |
| Local archive search (keyword / classic search path) | **Stable** | SQLite-backed search is part of the long-standing core architecture and remains inside the tested product path. |
| AI-assisted memory search | **Usable** | The feature is integrated, stores richer signals, and returns human-readable match reasons. It works in real flows, but model quality still matters and deeper benchmark work is still queued. |
| TUI | **Usable** | The TUI launches and remains a meaningful interface, but current validation is still closer to a start-smoke than to full interaction-depth coverage. |
| Webhook plugin | **Usable** | The plugin is a sensible extension path for Slack, Discord, n8n, Zapier, or custom endpoints, and the route has now been re-verified against a live local HTTP endpoint with a real POST payload. External third-party targets are still less proven than the core local flow, so this is not yet "stable". |
| Browser extension | **Deferred** | Intentionally removed from the near-term roadmap to keep focus on the core local intake, review, and search flow. |
| Email inlet | **Deferred** | Still a valid future extension, but not part of the current core product promise. |

## What this means for users

If you want the safest current path, think in this order:

1. install Kurier
2. run `kurier init`
3. run `kurier doctor --fix`
4. use `kurier add`, `kurier watch`, dashboard review, and local search as the main workflow

That path is where the product is strongest today.

If you want to demo the project honestly, the best framing is:

- core local capture, routing, review, and search: ready to show
- AI memory search: ready to show with clear caveats about model quality
- webhook integrations: promising extension, but not yet a fully proven flagship path

## What should likely happen next

The most useful next validation step would be a real third-party webhook smoke test against one representative external target such as Slack, Discord, n8n, or a hosted generic endpoint.

Why that matters:

- it would turn the webhook path from "locally proven" into something much easier to claim confidently across real integrations
- it keeps product work focused on trust and proof, not just adding more surface area

After that, the next quality step would be deeper evaluation work for AI-assisted memory search, especially around multilingual and German-heavy usage.
