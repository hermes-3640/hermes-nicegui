"""The joint layer of indirection: one facade for "the hermes daemon and
profiles" that every plugin reaches through instead of hand-combining a
``HermesExecutor`` and a profile name at each call site.

Reads go straight at the daemon's own SQLite databases/JSON files
(``HermesExecutor.read_sqlite``/``read_json``) -- fast, and correct alongside
the live daemon process since those are always opened read-only. Mutations
delegate to ``hermes_cli``, which runs the actual `hermes` CLI -- the thing
that safely maintains the daemon's own invariants (message counts, FTS
triggers, cron ticker locks, ...).

No NiceGUI import here (mirrors ``gateway.py``/``hermes_cli.py``): plugins
reach this through ``hermes_nicegui.web.current_store()``, which is the only
place that knows about ``app.storage``/the active browser tab.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from hermes_nicegui import hermes_cli
from hermes_nicegui.executor import HermesExecutor
from hermes_nicegui.gateway import (
    Comment,
    HermesError,
    Job,
    Message,
    Session,
    Task,
    TaskDetail,
)

# `Session.from_json` reads `last_active`, but the on-disk column is
# `last_activity_at` -- aliased explicitly rather than relying on callers to
# know the storage-layer name. `:search`, when given, is a `LIKE` pattern
# (caller wraps the raw query in `%...%`) matched against title, source, or
# any message in the session -- a superset of the `preview` column (the
# first user message only), so a hit inside a later message still surfaces
# the session, same as substring-matching the old, fully-materialized list
# in Python did.
_SESSIONS_LIST_SQL = """
SELECT s.*, s.last_activity_at AS last_active, (
    SELECT content FROM messages m
    WHERE m.session_id = s.id AND m.role = 'user' AND m.content IS NOT NULL
    ORDER BY m.id LIMIT 1
) AS preview
FROM sessions s
WHERE (:source IS NULL OR s.source = :source)
  AND (
    :search IS NULL
    OR s.title LIKE :search
    OR s.source LIKE :search
    OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.content LIKE :search)
  )
ORDER BY s.last_activity_at DESC
LIMIT :limit OFFSET :offset
"""
_SESSIONS_COUNT_SQL = """
SELECT COUNT(*) AS n FROM sessions s
WHERE (:source IS NULL OR s.source = :source)
  AND (
    :search IS NULL
    OR s.title LIKE :search
    OR s.source LIKE :search
    OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id AND m.content LIKE :search)
  )
"""

_SESSION_ROW_SQL = "SELECT *, last_activity_at AS last_active FROM sessions WHERE id = ?"
_SESSION_MESSAGES_SQL = "SELECT * FROM messages WHERE session_id = ? ORDER BY id"


def _message_from_row(row: dict) -> Message:
    """`messages.tool_calls` is a JSON-encoded column on disk; ``Message``
    wants it already parsed (mirrors what `Message.from_json` expects from
    the old gateway API's already-decoded JSON body)."""
    data = dict(row)
    if data.get("tool_calls"):
        data["tool_calls"] = json.loads(data["tool_calls"])
    return Message.from_json(data)


@dataclass
class SessionsStore:
    executor: HermesExecutor
    profile: str

    async def list(
        self,
        *,
        source: str | None = None,
        search: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> tuple[list[Session], int]:
        params = {
            "source": source,
            "search": f"%{search}%" if search else None,
            "limit": limit,
            "offset": offset,
        }
        rows = await self.executor.read_sqlite(
            "state.db", _SESSIONS_LIST_SQL, params, profile=self.profile
        )
        count_rows = await self.executor.read_sqlite(
            "state.db",
            _SESSIONS_COUNT_SQL,
            {"source": params["source"], "search": params["search"]},
            profile=self.profile,
        )
        total = count_rows[0]["n"] if count_rows else 0
        return [Session.from_json(row) for row in rows], total

    async def get_detail(self, session_id: str) -> tuple[Session, list[Message]]:
        session_rows = await self.executor.read_sqlite(
            "state.db", _SESSION_ROW_SQL, (session_id,), profile=self.profile
        )
        if not session_rows:
            raise HermesError(f"session {session_id} not found")
        message_rows = await self.executor.read_sqlite(
            "state.db", _SESSION_MESSAGES_SQL, (session_id,), profile=self.profile
        )
        session = Session.from_json(session_rows[0])
        messages = [_message_from_row(row) for row in message_rows]
        return session, messages

    async def rename(self, session_id: str, title: str) -> None:
        await hermes_cli.rename_session(self.executor, self.profile, session_id, title)

    async def delete(self, session_id: str) -> None:
        await hermes_cli.delete_session(self.executor, self.profile, session_id)


@dataclass
class CronStore:
    executor: HermesExecutor
    profile: str

    async def list_jobs(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[list[Job], int]:
        """`cron/jobs.json` is one small file with no partial-read story of
        its own, so "offset/limit" here means slicing the fully-parsed list
        in Python rather than a database query -- same kwargs shape as
        `SessionsStore.list`/`KanbanStore.list_tasks` so every plugin list
        page's pagination looks the same from the UI side, even though only
        the SQL-backed stores can push the slicing down to the read itself.
        """
        data = await self.executor.read_json("cron/jobs.json", profile=self.profile)
        jobs = [Job.from_json(j) for j in (data or {}).get("jobs", [])]
        total = len(jobs)
        page = jobs[offset:] if limit is None else jobs[offset : offset + limit]
        return page, total

    async def get_job(self, job_id: str) -> Job:
        jobs, _total = await self.list_jobs(limit=None)
        for job in jobs:
            if job.id == job_id:
                return job
        raise HermesError(f"cron job {job_id} not found")

    async def create(
        self,
        *,
        schedule: str,
        prompt: str = "",
        name: str | None = None,
        deliver: str | None = None,
        skills: list[str] | None = None,
        repeat: int | None = None,
    ) -> None:
        await hermes_cli.create_job(
            self.executor,
            self.profile,
            schedule=schedule,
            prompt=prompt,
            name=name,
            deliver=deliver,
            skills=skills,
            repeat=repeat,
        )

    async def save_fields(self, job_id: str, fields: dict) -> Job:
        """Apply `fields` (whatever subset of name/schedule/prompt/deliver/
        skills/repeat is present -- the cron detail page's form and
        raw-YAML tabs both funnel through here) via `hermes cron edit`, then
        re-read the job so the caller sees the daemon's own resulting
        state."""
        await hermes_cli.edit_job(
            self.executor,
            self.profile,
            job_id,
            name=fields.get("name"),
            schedule=fields.get("schedule"),
            prompt=fields.get("prompt"),
            deliver=fields.get("deliver"),
            skills=fields.get("skills"),
            repeat=fields.get("repeat"),
        )
        return await self.get_job(job_id)

    async def pause(self, job_id: str) -> Job:
        await hermes_cli.pause_job(self.executor, self.profile, job_id)
        return await self.get_job(job_id)

    async def resume(self, job_id: str) -> Job:
        await hermes_cli.resume_job(self.executor, self.profile, job_id)
        return await self.get_job(job_id)

    async def delete(self, job_id: str) -> None:
        await hermes_cli.delete_job(self.executor, self.profile, job_id)

    async def run(self, job_id: str) -> Job:
        await hermes_cli.run_job(self.executor, self.profile, job_id)
        return await self.get_job(job_id)


_KANBAN_TASK_COLUMNS = """
    t.*,
    (SELECT COUNT(*) FROM task_comments c WHERE c.task_id = t.id) AS comment_count,
    (SELECT r.summary FROM task_runs r
     WHERE r.task_id = t.id AND r.summary IS NOT NULL
     ORDER BY r.id DESC LIMIT 1) AS latest_summary
"""
_KANBAN_LIST_SQL = f"""
SELECT {_KANBAN_TASK_COLUMNS} FROM tasks t
WHERE (:status IS NULL OR t.status = :status)
ORDER BY t.priority DESC, t.created_at ASC
LIMIT :limit OFFSET :offset
"""
_KANBAN_COUNT_SQL = (
    "SELECT COUNT(*) AS n FROM tasks t WHERE (:status IS NULL OR t.status = :status)"
)
_KANBAN_TASK_SQL = f"SELECT {_KANBAN_TASK_COLUMNS} FROM tasks t WHERE t.id = :task_id"
_KANBAN_COMMENTS_SQL = "SELECT * FROM task_comments WHERE task_id = ? ORDER BY id"
_KANBAN_RUNS_SQL = "SELECT * FROM task_runs WHERE task_id = ? ORDER BY id"


@dataclass
class KanbanStore:
    """Kanban's board is daemon-global, not profile-scoped (confirmed
    against the real host: `kanban.db` lives at the daemon's root, not under
    any `profiles/<name>/`), so unlike `SessionsStore`/`CronStore` this
    takes no `profile` -- reads always go to `profile=""` (the root).

    Read-only on purpose: kanban's CLI (`hermes kanban ...`) is a
    task-lifecycle tool (block/unblock/promote/complete/...), not a
    generic field-setter, so it doesn't cover what the board UI's
    create/edit/delete/comment/dispatch actions need. Those keep going
    through `KanbanClient` (dashboard REST) -- see `plugins/kanban/ui.py`.
    """

    executor: HermesExecutor

    async def list_tasks(
        self, *, status: str | None = None, limit: int = 30, offset: int = 0
    ) -> tuple[list[Task], int]:
        """`status=None` ("all") lists across every status; otherwise scoped
        to just that one -- `plugins/kanban/ui.py`'s status tabs pass their
        own filter through here rather than fetching everything and
        re-filtering client-side, so a status tab's own page count reflects
        only tasks in that status."""
        params = {"status": status, "limit": limit, "offset": offset}
        rows = await self.executor.read_sqlite("kanban.db", _KANBAN_LIST_SQL, params)
        count_rows = await self.executor.read_sqlite(
            "kanban.db", _KANBAN_COUNT_SQL, {"status": status}
        )
        total = count_rows[0]["n"] if count_rows else 0
        return [Task.from_json(row) for row in rows], total

    async def get_task_detail(self, task_id: str) -> TaskDetail:
        rows = await self.executor.read_sqlite("kanban.db", _KANBAN_TASK_SQL, {"task_id": task_id})
        if not rows:
            raise HermesError(f"task {task_id} not found")
        comment_rows = await self.executor.read_sqlite(
            "kanban.db", _KANBAN_COMMENTS_SQL, (task_id,)
        )
        run_rows = await self.executor.read_sqlite("kanban.db", _KANBAN_RUNS_SQL, (task_id,))
        return TaskDetail(
            task=Task.from_json(rows[0]),
            comments=[Comment.from_json(row) for row in comment_rows],
            runs=run_rows,
        )


@dataclass
class HermesStore:
    sessions: SessionsStore
    cron: CronStore
    kanban: KanbanStore


def build_store(executor: HermesExecutor, profile: str) -> HermesStore:
    return HermesStore(
        sessions=SessionsStore(executor, profile),
        cron=CronStore(executor, profile),
        kanban=KanbanStore(executor),
    )
