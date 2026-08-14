"""Reaches a Hermes daemon: `hermes` CLI subprocess *and* read-only access to
its on-disk state, local or over SSH.

`HermesExecutor` is the one place that knows how to reach a given daemon.
Mutations (rename/delete a session, pause/resume/create a cron job, ...) run
the `hermes` CLI itself (`run`/`argv`) -- see `hermes_nicegui/hermes_cli.py`
for what actually gets invoked -- since that's what safely maintains the
daemon's own invariants (message counts, FTS triggers, cron ticker locks).
Reads (session/message lists, cron job listings, profile discovery) bypass
the CLI and go straight at the daemon's own SQLite databases and JSON files
under its data directory (`~/.hermes` by default -- see
`Settings.hermes_home`) via `read_sqlite`/`read_json`/`list_profile_names`,
confirmed live against a real Hermes install (`hermesagent.lan`): shelling
out to `hermes sessions export` per page load was too slow to matter on a
real profile, and every one of these reads is available directly on disk
anyway. All read methods open SQLite read-only (`mode=ro`) -- they can never
write, which is what makes it safe to read alongside the live daemon process
without touching its locking.

Local reads run in a thread (`asyncio.to_thread`) so they never block the
event loop, matching the async-only rule the rest of this app follows. SSH
reads run a small stdlib-only `python3 -c` script on the remote host (fed a
JSON request over stdin, replying with a JSON value on stdout) -- no new
remote dependency, and no shell-escaping of the query/path since nothing is
interpolated into the script text itself.

The CLI-subprocess argv-building path (`argv`) serves the interactive chat
pty (`plugins/sessions/ui.py`) and one-shot mutations (`run`) alike, but only
the former passes `tty=True`. Forcing a remote pty (`ssh -tt`) on a one-shot,
non-interactive command hangs indefinitely -- confirmed against the real
host: `hermes sessions export ...` over `ssh -tt` never returns, while the
identical command over plain `ssh` completes in well under a second. `-tt`
is only for `hermes chat`, which genuinely needs a remote tty for its own
readline/cursor handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from hermes_nicegui.gateway import HermesError


@dataclass
class CompletedResult:
    returncode: int
    stdout: str
    stderr: str


async def _run_subprocess(argv: list[str], *, input_text: str | None = None) -> CompletedResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input_text.encode() if input_text is not None else None)
    return CompletedResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )


# -- local read primitives ---------------------------------------------------
#
# Run inside `asyncio.to_thread` by `HermesExecutor`'s read methods -- plain
# blocking stdlib calls, no async needed at this level.


def _read_sqlite_local(path: str, sql: str, params: Sequence[Any] | dict[str, Any]) -> list[dict]:
    resolved = os.path.expanduser(path)
    if not os.path.exists(resolved):
        return []
    con = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


def _read_text_local(path: str) -> str | None:
    resolved = os.path.expanduser(path)
    if not os.path.exists(resolved):
        return None
    with open(resolved, encoding="utf-8") as f:
        return f.read()


def _listdir_local(path: str) -> list[str]:
    resolved = os.path.expanduser(path)
    if not os.path.isdir(resolved):
        return []
    return os.listdir(resolved)


# -- remote read primitive ---------------------------------------------------
#
# One script template, dispatched by an "op" field -- keeps the ssh round
# trip to a single helper instead of one script per operation.
#
# The request is embedded *inside* the generated script text (as a Python
# string literal, safely escaped by a plain `json.dumps` round-trip) rather
# than sent separately over stdin, and the whole script is piped to `python3
# -` (read the program from stdin) rather than passed as a `-c <script>`
# argv element. Both choices exist for the same reason: `ssh host cmd arg1
# arg2 ...` does not preserve argv boundaries the way a local
# `create_subprocess_exec` does -- it joins everything after the host into
# one string and hands it to the *remote* shell, which then re-tokenizes it.
# A multi-line script full of parens/quotes handed to `-c` that way breaks
# immediately (confirmed live: `bash: -c: line 4: syntax error near
# unexpected token`), and there's no quoting scheme that survives an opaque
# intermediate shell reliably. Piping the whole thing over stdin sidesteps
# shell tokenization entirely -- nothing about the script's contents is ever
# interpreted as shell syntax, local or remote.
#
# `os.path.expanduser` runs *on the remote host*, resolving `~` against
# whatever user the ssh session authenticates as (sshd sets `$HOME` even for
# a non-interactive command, confirmed against the real host), mirroring
# what the local read primitives above do in-process.

_REMOTE_IO_SCRIPT_TEMPLATE = """
import json, os, sqlite3

req = json.loads({request_json})
op = req["op"]
path = os.path.expanduser(req["path"])

if op == "sqlite":
    if not os.path.exists(path):
        result = []
    else:
        con = sqlite3.connect(f"file:{{path}}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute(req["sql"], req.get("params") or [])
        result = [dict(row) for row in cur.fetchall()]
        con.close()
elif op == "read_text":
    result = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            result = f.read()
elif op == "listdir":
    result = os.listdir(path) if os.path.isdir(path) else []
else:
    raise ValueError(f"unknown op: {{op}}")

print(json.dumps(result))
"""


def _build_remote_io_script(request: dict[str, Any]) -> str:
    # `json.dumps` twice: the inner call turns `request` into a JSON string;
    # the outer call turns *that string* into a properly quoted/escaped
    # Python string literal to embed in the template, so `json.loads(...)`
    # in the generated script reconstructs it exactly.
    return _REMOTE_IO_SCRIPT_TEMPLATE.format(request_json=json.dumps(json.dumps(request)))


class HermesExecutor:
    def __init__(
        self,
        *,
        mode: str = "local",
        hermes_bin: str = "hermes",
        ssh_target: str = "",
        ssh_options: list[str] | None = None,
        hermes_home: str = "~/.hermes",
        run_fn: Callable[[list[str]], Awaitable[CompletedResult]] | None = None,
        io_run_fn: Callable[[list[str], str], Awaitable[CompletedResult]] | None = None,
    ) -> None:
        """``run_fn``/``io_run_fn`` are injectable so tests can fake the
        actual subprocess launch without touching a real process -- mirrors
        ``transport`` on ``HermesClient``/``KanbanClient`` (fake at the
        subprocess boundary, exercise the real class above it). Both default
        to real subprocess launches; ``io_run_fn`` additionally writes
        ``input_text`` to the child's stdin (see ``_run_remote_io``).
        """
        if mode not in ("local", "ssh"):
            raise ValueError(f"unknown exec mode: {mode!r}")
        if mode == "ssh" and not ssh_target:
            raise ValueError("exec_mode='ssh' requires ssh_target to be set")
        self.mode = mode
        self.hermes_bin = hermes_bin
        self.ssh_target = ssh_target
        self.ssh_options = ssh_options or []
        self.hermes_home = hermes_home
        self._run_fn = run_fn or _run_subprocess
        self._io_run_fn = io_run_fn or (
            lambda argv, input_text: _run_subprocess(argv, input_text=input_text)
        )

    def argv(self, *args: str, profile: str = "", tty: bool = False) -> list[str]:
        """Full argv for `hermes *args`, local or ssh.

        `-p <profile>` is spliced in right after the binary (before `args`)
        when `profile` is set and isn't the implicit "default" profile --
        matches the real invocation confirmed against the profile alias
        scripts (`~/.local/bin/<name>` -> `hermes -p <name> "$@"`).

        `tty` controls whether ssh forces a remote pty (`-tt`) -- only
        `hermes chat` (run through `PtySession`) sets it. Forcing one for a
        one-shot command hangs indefinitely (confirmed against the real
        host), so `run()` never passes it.
        """
        profile_flag = ["-p", profile] if profile and profile != "default" else []
        hermes_argv = [self.hermes_bin, *profile_flag, *args]
        if self.mode == "local":
            return hermes_argv
        ssh_flags = ["-tt"] if tty else []
        return ["ssh", *ssh_flags, *self.ssh_options, self.ssh_target, *hermes_argv]

    async def run(self, *args: str, profile: str = "") -> CompletedResult:
        """Run `hermes *args` to completion and capture stdout/stderr."""
        return await self._run_fn(self.argv(*args, profile=profile))

    # -- reads: daemon data dir, not the CLI ---------------------------------

    def profile_home(self, profile: str = "") -> str:
        """The daemon's data directory for `profile` (`~/.hermes` for the
        implicit default, `~/.hermes/profiles/<name>` otherwise)."""
        base = self.hermes_home.rstrip("/")
        if not profile or profile == "default":
            return base
        return f"{base}/profiles/{profile}"

    def _remote_io_argv(self) -> list[str]:
        # `python3 -` (read the program from stdin), not `-c <script>` -- see
        # the module-level comment above `_REMOTE_IO_SCRIPT_TEMPLATE` for why
        # a `-c` argv element doesn't survive being relayed through ssh.
        return ["ssh", *self.ssh_options, self.ssh_target, "python3", "-"]

    async def _run_remote_io(self, request: dict[str, Any]) -> Any:
        script = _build_remote_io_script(request)
        result = await self._io_run_fn(self._remote_io_argv(), script)
        if result.returncode != 0:
            raise HermesError(f"remote read failed ({request['op']}): {result.stderr[:500]}")
        return json.loads(result.stdout)

    async def read_sqlite(
        self,
        relative_db_path: str,
        sql: str,
        params: Sequence[Any] | dict[str, Any] = (),
        *,
        profile: str = "",
    ) -> list[dict]:
        """Read-only query against a daemon SQLite database.

        `relative_db_path` is relative to `profile_home(profile)`, e.g.
        `"state.db"` or `"cron/executions.db"`. Pass `profile=""` for
        daemon-global databases that live outside any profile dir (e.g.
        `kanban.db`). `params` may be positional (a sequence, for `?`
        placeholders) or named (a dict, for `:name` placeholders) -- passed
        through to `sqlite3`'s own `execute(sql, params)` unchanged either
        way, so don't coerce it to a list here (that would silently turn a
        dict's *keys* into positional params instead).
        """
        path = f"{self.profile_home(profile)}/{relative_db_path}"
        if self.mode == "local":
            return await asyncio.to_thread(_read_sqlite_local, path, sql, params)
        request = {"op": "sqlite", "path": path, "sql": sql, "params": params}
        return await self._run_remote_io(request)

    async def read_json(self, relative_path: str, *, profile: str = "") -> Any:
        """Parsed JSON contents of a file under `profile_home(profile)`, or
        `None` if it doesn't exist."""
        path = f"{self.profile_home(profile)}/{relative_path}"
        if self.mode == "local":
            text = await asyncio.to_thread(_read_text_local, path)
        else:
            text = await self._run_remote_io({"op": "read_text", "path": path})
        return json.loads(text) if text is not None else None

    async def list_profile_names(self) -> list[str]:
        """Every profile name the daemon knows about, `"default"` first.

        Replaces parsing `hermes profile list`'s table -- profiles are just
        directories under `~/.hermes/profiles/` (confirmed against the real
        host), one per non-default profile.
        """
        base = f"{self.hermes_home.rstrip('/')}/profiles"
        if self.mode == "local":
            names = await asyncio.to_thread(_listdir_local, base)
        else:
            names = await self._run_remote_io({"op": "listdir", "path": base})
        return ["default", *sorted(n for n in names if not n.startswith("."))]
