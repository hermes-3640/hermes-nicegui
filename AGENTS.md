# AGENTS.md

Modular NiceGUI web UI for the [Hermes Agent](https://github.com/NousResearch/hermes-agent)
daemon, profile-scoped (a per-tab profile switcher lets each browser tab
target a different Hermes profile). Python 3.11+, managed with uv.

## Commands

```sh
uv sync                 # install deps + the package (editable), incl. dev group
uv run hermes-nicegui    # or: uv run python main.py
uv run pytest             # whole suite, offline, no browser, no real daemon
uv run pytest tests/test_sessions_list.py   # one file
uv run pyright            # type check
uv run ruff check .       # lint
uv run ruff format .      # format
```

Copy `.env.example` to `.env` first before running the real app. Tests never
read `.env` or any environment variable — see Testing below.

## Layout

- `src/hermes_nicegui/app.py` — process entrypoint only: `Settings()` from the
  environment, the real `HermesClient`/`HermesExecutor`, `ui.run()`. Nothing
  else imports this.
- `src/hermes_nicegui/web.py` — the UI shell and the boundary plugins depend
  on: `frame()` (header + nav drawer + profile switcher), the process-wide
  `AppState`, `build(context, plugins=None)`, and `current_profile()`/
  `current_executor()`/`current_store()` — the per-tab-profile-aware
  accessors every plugin reaches through.
- `src/hermes_nicegui/executor.py` — `HermesExecutor`: the one class that
  knows how to reach a given Hermes daemon, local or over SSH. Runs the
  `hermes` CLI as a subprocess for mutations (`run`/`argv`) *and* reads the
  daemon's own SQLite databases/JSON files directly (`read_sqlite`/
  `read_json`/`list_profile_names`) for everything that doesn't need to go
  through the CLI. See its module docstring for why reads bypass the CLI
  entirely (speed, and no dependence on the CLI's text output shape).
- `src/hermes_nicegui/hermes_cli.py` — thin `hermes <subcommand>` argv
  builders + wrappers, *mutations only* (rename/delete a session, cron
  create/edit/pause/resume/remove/run). Reads don't live here.
- `src/hermes_nicegui/store.py` — the facade every plugin actually calls
  through (`web.current_store()`): `SessionsStore`/`CronStore`/`KanbanStore`,
  bundled as `HermesStore`. Owns the SQL/JSON parsing for reads and
  delegates mutations to `hermes_cli`. This is "the one place that deals
  with the hermes daemon and profiles" — if a plugin needs new data from the
  daemon, it's a new method here, not a new ad hoc client.
- `src/hermes_nicegui/gateway.py` — `HermesClient` (the gateway's bearer-token
  REST API — mostly unused now; kept for anything that still needs it) plus
  the shared dataclasses (`Session`, `Message`, `Job`, `Task`, `Board`,
  `Comment`, `TaskDetail`) used by both direct-SQLite reads (`store.py`) and
  the dashboard REST clients (`plugins/{cron,kanban}/gateway.py`). No NiceGUI
  import in it.
- `src/hermes_nicegui/dashboard_auth.py` — cookie-auth HTTP primitive for the
  Hermes CLI's own dashboard web server. Only `plugins/kanban/gateway.py`
  (`KanbanClient`) still uses it — kanban's board CLI has no generic
  field-setter, so its writes stay on this REST API; everything else that
  used to (cron) has moved to direct daemon access.
- `src/hermes_nicegui/config.py` — `Settings` (pydantic-settings, `HERMES_`
  env prefix): gateway/kanban connection info, `exec_mode`/`ssh_target`/
  `hermes_home` (how/where to reach the daemon), auth, plugin config.
- `src/hermes_nicegui/plugins/<name>/` — one subpackage per plugin:
  `sessions` (browse sessions + live chat pty), `cron` (list/create/edit
  scheduled jobs, fully off dashboard REST — see below), `kanban`
  (board/task CRUD — reads via `store.py`, writes via the dashboard's
  `/api/plugins/kanban`), `terminal` (a plain local shell pty), `files`
  (browse/edit files under a configured root). `ui.py` is NiceGUI wiring;
  `logic.py` is pure functions pulled out of it so they're unit-testable
  with no browser.

## Rules that differ from defaults

- **Plugins depend on `hermes_nicegui.plugin` and `hermes_nicegui.web` only.**
  Never `hermes_nicegui.app`, never another plugin's package. `app.py` is the
  process entrypoint, not a library — importing from it couples a plugin to
  how the *production* process happens to boot, which is exactly what breaks
  when you want to test a plugin standalone. This is what lets you edit the
  sessions plugin without risking the shell, and edit the shell without
  reading every plugin. If a plugin ever needs another plugin's data, route
  it through `PluginContext`/`web.current_store()`, not a direct cross-plugin
  import. The one plugin that bends this is `kanban`: its board *writes* have
  no CLI/direct-DB equivalent, so it builds and owns a second client
  (`plugins/kanban/gateway.py::KanbanClient`) in its own `__init__` rather
  than being handed one — still no import of `app.py` or another plugin,
  just a second client scoped to the one plugin that needs it.
- **Check `HermesExecutor`/`store.py` before reaching for a new client.**
  If you're extending sessions/cron/kanban and need new daemon data, check
  whether it's already readable off `state.db`/`cron/jobs.json`/`kanban.db`
  (add a method to the relevant `*Store` in `store.py`) or needs a CLI
  mutation (add a wrapper in `hermes_cli.py`) before writing a new HTTP
  client — that's the default assumption for this app now, not an exception.
- **Reads and writes deliberately take different paths.** Reads open SQLite
  read-only (`mode=ro`) directly against the daemon's own files — safe
  alongside the live daemon process since a read-only connection can never
  write. Mutations always go through the `hermes` CLI (or, for kanban only,
  the dashboard REST API) — never write SQL directly against a daemon
  database, even for something that looks like a simple `UPDATE`; the daemon
  itself owns invariants a raw write could skip (message counts, FTS
  triggers, cron ticker locks, kanban's dispatcher/claim state machine).
- **Pure logic lives outside `ui.py`.** Anything that doesn't touch NiceGUI or
  `app.storage` (formatting, filtering, unread-state math) belongs in a
  sibling `logic.py`, as public functions, so it can be tested directly
  instead of only through the `user` test harness.
- **`AppState` is a deliberate module-level global** (`hermes_nicegui.web.state`),
  not threaded through every page function. It holds process-wide data (nav
  items, settings, the shared `HermesExecutor`) that's genuinely the same for
  every user session, not per-user state — that's what `app.storage.user`
  (per-browser) and `app.storage.client` (per-tab, see `current_profile()`)
  are for. `web.build()` resets it in place, which is what makes calling
  `build()` again from a second test safe.

## Testing quirks

- **No `main.py` in tests** (`main_file = ""` in `pyproject.toml`). A test
  builds a `PluginContext` via the `context` or `make_context` fixture
  (`tests/conftest.py`), constructs the plugin(s) it wants, and calls
  `hermes_nicegui.web.build(context, [SomePlugin(context)])` itself — inside
  the test function, not a fixture, so the plugin set being exercised is
  visible right there. `tests/test_plugin_discovery.py` is the one test that
  calls `web.build(context)` with no explicit list, to check entry-point
  discovery still resolves to what we ship.
- **Reads hit real temp files, not a fake.** `hermes_home` (a fresh
  `tmp_path` per test) is where `fake_hermes_cli`/`fake_kanban` seed a real
  `state.db`/`cron/jobs.json`/`kanban.db` with the fixture's schema, and
  `executor`/`make_executor` build a *real*, local-mode `HermesExecutor`
  pointed at it — `read_sqlite`/`read_json`/`list_profile_names` are
  exercised for real, not faked. Only the CLI-subprocess *mutation* boundary
  is faked (`FakeHermesCli.run_fn`), and its handlers write through to those
  same files (real `UPDATE`/`DELETE` SQL, real JSON rewrite) so a read
  afterwards sees the effect, mirroring how the real `hermes` CLI mutates
  the daemon's own files in place. Kanban's dashboard-REST fake
  (`FakeKanban`) does the same dual-write into `kanban.db` for its
  mutations, since a real dashboard write and a real direct-SQLite read
  both ultimately hit the same on-disk database in production.
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

- **`ssh host cmd arg1 arg2 ...` does not preserve argv boundaries.** ssh
  joins everything after the target into a single string and hands it to
  the *remote* shell, which re-tokenizes it — so a multi-line Python script
  passed as a `-c <script>` argv element breaks the moment it contains
  quotes/parens/newlines (confirmed live: a syntax error from the remote
  `bash -c`). `HermesExecutor`'s remote-read primitive (`_run_remote_io`)
  works around this by piping the whole generated script over stdin to
  `python3 -` instead — nothing about its contents is ever shell-tokenized,
  locally or remotely. Keep this in mind before adding any new remote
  one-liner: prefer stdin over baking content into argv.
- **A forced remote pty (`ssh -tt`) on a one-shot command hangs indefinitely**
  — confirmed against the real host: `hermes sessions export ...` over
  `ssh -tt` never returns, while the identical command over plain `ssh`
  completes in well under a second. `HermesExecutor.argv`'s `tty` flag is
  only ever `True` for the interactive chat pty; `run()` (every one-shot
  mutation) never passes it.
- **Local-mode reads are fast (no subprocess); remote (`ssh`) reads still
  pay a real network round trip.** A page load that fetches from a
  slow/large remote profile must not block NiceGUI's own page
  `response_timeout` (default 3s) — see `sessions/ui.py`'s
  `await ui.context.client.connected()` guard before the fetch, and
  `tests/test_sessions_list.py::test_slow_fetch_does_not_block_initial_response`
  (which fakes a slow `io_run_fn` in `ssh` mode, since local reads are too
  fast to exercise this guard at all).
- **Model routing**: sessions created through the gateway API default to the
  `hermes-agent` model name, which the router may reject
  (`HTTP 401: Model hermes-agent is not supported`). Set `HERMES_DEFAULT_MODEL`
  to a routable model for your provider, e.g. `deepseek/deepseek-v4-flash`
  with `HERMES_DEFAULT_PROVIDER=openrouter`.
- **The kanban plugin's board CLI (`hermes kanban ...`) has no generic
  field-setter** — it's a task-lifecycle tool (block/unblock/promote/
  complete/request-review/...), not a "set title/body/priority/assignee" or
  "move to arbitrary status" verb, unlike cron's `hermes cron edit`. That's
  why kanban is the one plugin still on dashboard REST for writes
  (`KanbanClient`, cookie session via `POST /auth/password-login` with
  `HERMES_KANBAN_USERNAME`/`HERMES_KANBAN_PASSWORD`) while its *reads* moved
  to direct SQLite (`kanban.db`, which — unlike `state.db`/`cron/jobs.json`
  — is daemon-global, not profile-scoped: confirmed against the real host,
  it lives at the daemon's root, not under any `profiles/<name>/`). `POST
  /tasks` always creates a task in the `ready` column (there's no `status`
  field on create) and the dashboard's dispatcher can claim a `ready` task
  and spawn a real agent worker within about a minute
  (`kanban.dispatch_interval_seconds`, ~60s) — `KanbanClient.create_task`'s
  `status=` kwarg does a follow-up PATCH if the caller wants the safer
  `triage` instead, which is what the "New task" dialog defaults to.
- **Live chat is its own page** (`/sessions/chat`, `/sessions/chat/{id}` to
  resume), not part of the session detail page — it runs `hermes chat`
  attached to a real pty (`plugins/terminal/logic.py::PtySession`, the same
  pattern the plain `terminal` plugin uses for a local shell), not a
  websocket/SSE bridge. Session detail (`/sessions/{id}`) is a read-only
  transcript with rename/delete/"Continue in chat".
