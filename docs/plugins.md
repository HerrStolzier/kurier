# Plugin Guide

Kurier uses `pluggy` to discover optional extensions. The external package name is `kurier`, but plugins integrate with the internal Python package path `arkiv`.

## What plugins can do

Plugins can hook into three parts of the pipeline:

- `pre_classify`: change extracted text before the LLM sees it
- `post_classify`: inspect the classification result
- `on_routed`: react after a successful route, for example by sending a webhook or notification. Fires exactly once per item, with the final (primary) route result.

The hook specifications live in `src/arkiv/plugins/spec.py`.

## Discovery

Kurier discovers plugins through Python entry points in the `arkiv.plugins` group.

```toml
[project.entry-points."arkiv.plugins"]
my-plugin = "my_arkiv_plugin"
```

After installation, Kurier loads plugins automatically through `src/arkiv/plugins/manager.py`.

## Minimal example

```python
from arkiv.plugins.spec import hookimpl


@hookimpl
def on_routed(path: str, destination: str, route_name: str) -> None:
    print(f"Routed {path} -> {destination} via {route_name}")
```

## Local development

From the repo root:

```bash
uv pip install -e ".[dev]"
uv pip install -e plugins/arkiv-webhook
```

Run the plugin tests separately because the plugin test package has its own root:

```bash
pytest --rootdir=plugins/arkiv-webhook --override-ini="testpaths=plugins/arkiv-webhook/tests" plugins/arkiv-webhook/tests/
```

## Configuration notes

- Kurier reads its config from `~/.config/kurier/config.toml`
- Empty `categories = []` means a route matches all categories
- Hook calls return lists when multiple plugins implement the same hook, so code should preserve the original value when no plugin returns a replacement

## Example plugin

The repository includes a first-party webhook plugin in `plugins/arkiv-webhook/README.md`.
