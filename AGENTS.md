# AGENTS.md

Modular NiceGUI web UI for the [Hermes Agent](https://github.com/NousResearch/hermes-agent)
gateway. Everything talks to the gateway server-side through an async `httpx`
client, so the browser never sees the API token. Python 3.11+, managed with uv.

## Commands

```sh
uv sync                 # install deps + the package (editable), incl. dev group
uv run hermes-nicegui    # or: uv run python main.py
uv run pytest             # whole suite, offline, no browser
uv run pytest tests/test_sessions_list.py   # one file
uv run pyright            # type check
uv run ruff check .       # lint
uv run ruff format .      # format
```

Copy `.env.example` to `.env` first and set `HERMES_GATEWAY_URL` /
`HERMES_API_TOKEN` before running the real app. Tests never read `.env` or
any environment variable — see Testing below.

## Layout

- `src/hermes_nicegui/app.py` — process entrypoint only: `Settings()` from the
  environment, the real `HermesClient`, `ui.run()`. Nothing else imports this.
- `src/hermes_nicegui/web.py` — the UI shell and the boundary plugins depend
  on: `frame()` (header + nav drawer), the process-wide `AppState`, and
  `build(context, plugins=None)`.
- `src/hermes_nicegui/plugin.py` — the `Plugin` base class, `PluginContext`,
  `NavItem`, and `load_plugins()` (entry-point discovery).
- `src/hermes_nicegui/gateway.py` — `HermesClient`, the async httpx wrapper.
  Has no NiceGUI import in it; it's the one module that could be reused by a
  non-web frontend.
- `src/hermes_nicegui/config.py` — `Settings` (pydantic-settings, `HERMES_`
  env prefix).
- `src/hermes_nicegui/plugins/<name>/` — one subpackage per plugin. Currently
  just `sessions`. `ui.py` is NiceGUI wiring; `logic.py` is pure functions
  pulled out of it so they're unit-testable with no browser.

## Rules that differ from defaults

- **Plugins depend on `hermes_nicegui.plugin` and `hermes_nicegui.web` only.**
  Never `hermes_nicegui.app`, never another plugin's package. `app.py` is the
  process entrypoint, not a library — importing from it couples a plugin to
  how the *production* process happens to boot, which is exactly what breaks
  when you want to test a plugin standalone. This is what lets you edit the
  sessions plugin without risking the shell, and edit the shell without
  reading every plugin. If a plugin ever needs another plugin's data, route
  it through `PluginContext` or the gateway, not a direct cross-plugin import.
- **Pure logic lives outside `ui.py`.** Anything that doesn't touch NiceGUI or
  `app.storage` (formatting, filtering, unread-state math) belongs in a
  sibling `logic.py`, as public functions, so it can be tested directly
  (`tests/test_unread.py`) instead of only through the `user` test harness.
- **`AppState` is a deliberate module-level global** (`hermes_nicegui.web.state`),
  not threaded through every page function. It holds process-wide data (nav
  items, settings) that's genuinely the same for every user session, not
  per-user state — that's what `app.storage.user` is for (see
  `plugins/sessions/ui.py::_unread_state`). `web.build()` resets it in place,
  which is what makes calling `build()` again from a second test safe.

## Testing quirks

- **No `main.py` in tests** (`main_file = ""` in `pyproject.toml`). A test
  builds a `PluginContext` via the `context` or `make_context` fixture
  (`tests/conftest.py`), constructs the plugin(s) it wants, and calls
  `hermes_nicegui.web.build(context, [SomePlugin(context)])` itself — inside
  the test function, not a fixture, so the plugin set being exercised is
  visible right there. `tests/test_plugin_discovery.py` is the one test that
  calls `web.build(context)` with no explicit list, to check entry-point
  discovery still resolves to what we ship.
- **`make_context(**kwargs)` builds `Settings` directly** — no environment
  variables, no `monkeypatch.setenv`. Need a specific setting for one test
  (`test_dark.py` wants `ui_dark=True`)? Pass it as a kwarg.
- **The gateway is faked per-context**, not through a global. `make_context`
  wires an `httpx.MockTransport` into a fresh `HermesClient` for that test's
  `PluginContext`; nothing monkeypatches `hermes_nicegui.gateway.DEFAULT_TRANSPORT`
  (which still exists as a module-level fallback, but tests don't rely on it).
- **`conftest.py` loads `nicegui.testing.user_plugin`** specifically (see
  `addopts` in `pyproject.toml`), not the full `nicegui.testing` plugin — the
  latter also pulls in the selenium-driven `screen` fixture, and this suite
  has no reason to depend on a real browser.
- Registering a page is the moment NiceGUI/FastAPI inspects its signature; a
  page that can't be built fails at `web.build()`, not at first render. That
  means `web.build(context, [SomePlugin(context)])` alone (even before
  `user.open(...)`) is worth asserting on if you're testing that a plugin at
  least boots.

## Gotchas

- **Model routing**: sessions created through the API default to the
  `hermes-agent` model name, which the router may reject
  (`HTTP 401: Model hermes-agent is not supported`). Set `HERMES_DEFAULT_MODEL`
  to a routable model for your provider, e.g. `deepseek/deepseek-v4-flash`
  with `HERMES_DEFAULT_PROVIDER=openrouter`.
- **All gateway I/O is async**; the event loop must never be blocked by a
  network call. Use `ui.timer` / `asyncio`, never `time.sleep`, in a page
  handler.
- **SSE streaming** (`HermesClient.stream_turn`) is consumed incrementally via
  `aiter_lines` and never buffers the whole response — don't collect it into
  a list before processing unless a test specifically wants the full
  transcript (see `test_stream_turn_yields_events`).
- **`_unread_state()` can raise `RuntimeError`** if `app.storage.user` isn't
  available (storage disabled in some test/embedding setups) — it's caught
  and falls back to an empty dict rather than crashing the page.
- **No `chat` plugin exists yet.** `HermesClient.stream_turn`/`chat_turn` are
  implemented and tested (`tests/test_gateway.py`), but there is no page for
  them. Don't trust a docs mention of "chat" as a plugin without checking
  `src/hermes_nicegui/plugins/` first.
