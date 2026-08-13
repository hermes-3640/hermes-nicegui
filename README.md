# hermes-nicegui

A modular, plugin-based [NiceGUI](https://nicegui.io) web UI for the
[Hermes Agent](https://github.com/NousResearch/hermes-agent) gateway.

Everything talks to the Hermes API server **server-side** through an async
`httpx` client, so the browser never sees the gateway token and there are no
CORS issues. The UI itself is a plugin host: self-contained modules (sessions,
chat, …) each expose a `register()` hook and are discovered through
`importlib.metadata` entry points.

## Stack

- [NiceGUI](https://nicegui.io) 3.x — UI framework
- [httpx](https://www.python-httpx.org/) — async gateway client
- [loguru](https://loguru.readthedocs.io/) — logging
- [uv](https://docs.astral.sh/uv/) — packaging / env management
- pytest + NiceGUI `user` fixture — fast, browserless UI tests

## Quick start

```bash
cp .env.example .env      # set HERMES_GATEWAY_URL + HERMES_API_TOKEN
uv sync                   # install deps + the package (editable)
uv run hermes-nicegui     # or: uv run python main.py
```

Then open http://127.0.0.1:8080.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `HERMES_GATEWAY_URL` | `http://127.0.0.1:8443` | Hermes API server base URL |
| `HERMES_API_TOKEN` | *(required)* | Bearer token for the API server |
| `HERMES_UI_HOST` | `0.0.0.0` | NiceGUI bind host |
| `HERMES_UI_PORT` | `8080` | NiceGUI bind port |
| `HERMES_UI_DARK` | `false` | Dark mode UI |
| `HERMES_DEFAULT_MODEL` | `hermes-agent` | Model to lock on new sessions |
| `HERMES_DEFAULT_PROVIDER` | *(none)* | Provider for the default model |
| `HERMES_PLUGINS_DISABLED` | *(none)* | Comma-separated plugin names to skip |
| `HERMES_LOG_LEVEL` | `INFO` | loguru level |

> **Model routing gotcha:** sessions created through the API default to the
> `hermes-agent` model name, which the router may reject
> (`HTTP 401: Model hermes-agent is not supported`). Set `HERMES_DEFAULT_MODEL`
> to a routable model for your provider, e.g. `deepseek/deepseek-v4-flash`
> with `HERMES_DEFAULT_PROVIDER=openrouter`. The chat plugin locks this model
> onto new sessions and sends it on every turn.

## Architecture

Two layers, and plugins are only allowed to depend on the outer one:

- **`hermes_nicegui.app`** — the process entrypoint. Reads `Settings()` from
  the environment, builds the real `HermesClient`, calls `ui.run()`. Nothing
  else imports this module.
- **`hermes_nicegui.web`** — the UI shell: the shared `frame()` (header + nav
  drawer), the process-wide `AppState` (nav items, settings), and
  `build(context, plugins=None)`, which registers the home page and every
  plugin's pages. `plugins=None` discovers the installed set through the
  `hermes_nicegui.plugins` entry-point group (the production path); an
  explicit list registers exactly those plugin instances (what the test
  suite does).

Plugins (`src/hermes_nicegui/plugins/`) import from `hermes_nicegui.plugin`
(the `Plugin`/`PluginContext`/`NavItem` types) and `hermes_nicegui.web`
(`frame`), never from `hermes_nicegui.app` and never from each other. That
boundary is what makes it possible to edit one plugin without risking another,
and it's what would let a plugin move out into its own installable package
later with no import changes.

A plugin is any Python package exposing a `plugin` entry-point value that
implements the `Plugin` protocol from `hermes_nicegui.plugin`:

- `name`, `title`, `icon` — identity + nav metadata
- `nav_items() -> list[NavItem]` — entries shown in the left drawer
- `register() -> None` — called once at startup to add pages

`PluginContext` carries the shared async `HermesClient`, the `loguru` logger,
and app settings, and is passed to `Plugin.__init__`. Built-in plugins live in
`src/hermes_nicegui/plugins/` and are wired through
`[project.entry-points."hermes_nicegui.plugins"]`. A third party plugin can be
installed with `uv add` and is discovered automatically.

Current plugins:

- `sessions` — paginated session list, message history, rename / fork / delete

> There is no `chat` plugin yet — streaming chat against `HermesClient.stream_turn`
> is implemented in the gateway client but has no page. If you're looking for
> it, it doesn't exist; this note exists so that claim doesn't quietly go
> stale again.

## Testing

```bash
uv run pytest
```

Tests use NiceGUI's `user` fixture (a simulated in-process browser), so they
are fast and need no Selenium. There is no `main.py` involved
(`main_file = ""` in `pyproject.toml`): each test builds a `PluginContext` via
the `context`/`make_context` fixtures (`tests/conftest.py`) — settings
constructed directly, gateway responses faked with `httpx.MockTransport`, no
environment variables, no network — and calls
`hermes_nicegui.web.build(context, [SomePlugin(context)])` itself, so it
registers exactly the plugin it's testing. `tests/test_plugin_discovery.py`
is the one test that instead calls `web.build(context)` with no explicit
list, checking that entry-point discovery itself still works.

## Type checking and linting

```bash
uv run pyright   # type checking
uv run ruff check .   # lint
uv run ruff format .  # format
```

`pyrightconfig.json` pins the interpreter to the project venv and the include
paths to `src`, `main.py`, and `tests`. If your editor's LSP shows unresolved
imports, point it at `.venv/bin/python`.

## Performance notes

- All gateway I/O is async (`httpx.AsyncClient`); the event loop is never
  blocked by network calls.
- SSE streaming is consumed incrementally (`aiter_lines`) and pushed to the UI
  via async page handlers.
- Use `ui.timer` / `asyncio` for background refresh — never `time.sleep` in a
  page handler.
