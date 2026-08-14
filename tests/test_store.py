"""Tests for hermes_nicegui.store: the SessionsStore/CronStore/KanbanStore
facade, against the real on-disk fixtures `fake_hermes_cli`/`fake_kanban`
seed under `hermes_home` (see tests/conftest.py) plus the fake `hermes`
CLI-subprocess mutation boundary."""

from __future__ import annotations

from hermes_nicegui.executor import HermesExecutor
from hermes_nicegui.store import build_store


async def test_sessions_list_includes_preview_and_last_active(executor: HermesExecutor) -> None:
    sessions, total = await build_store(executor, "").sessions.list()
    assert [s.id for s in sessions] == ["sess-1", "sess-2"]
    assert sessions[0].preview == "How do I access your API?"
    assert sessions[0].last_active == 1786620319.0
    assert total == 2


async def test_sessions_list_paginates_and_searches(executor: HermesExecutor) -> None:
    store = build_store(executor, "")
    sessions, total = await store.sessions.list(limit=1, offset=0)
    assert [s.id for s in sessions] == ["sess-1"]
    assert total == 2

    sessions, total = await store.sessions.list(limit=1, offset=1)
    assert [s.id for s in sessions] == ["sess-2"]
    assert total == 2

    sessions, total = await store.sessions.list(search="cron")
    assert [s.id for s in sessions] == ["sess-2"]
    assert total == 1


async def test_sessions_get_detail_parses_tool_calls(executor: HermesExecutor) -> None:
    session, messages = await build_store(executor, "").sessions.get_detail("sess-1")
    assert session.id == "sess-1"
    assert len(messages) == 3
    assert messages[0].role == "user"
    tool_calls = messages[2].tool_calls
    assert tool_calls is not None
    assert tool_calls[0]["function"]["name"] == "terminal"


async def test_sessions_rename_and_delete_go_through_cli(
    executor: HermesExecutor, fake_hermes_cli
) -> None:
    store = build_store(executor, "").sessions
    await store.rename("sess-1", "New title")
    sessions, _total = await store.list()
    assert next(s for s in sessions if s.id == "sess-1").title == "New title"

    await store.delete("sess-2")
    sessions, total = await store.list()
    assert [s.id for s in sessions] == ["sess-1"]
    assert total == 1


async def test_cron_list_jobs_reads_jobs_json(executor: HermesExecutor) -> None:
    jobs, total = await build_store(executor, "").cron.list_jobs()
    assert [j.id for j in jobs] == ["aabbccddeeff"]
    assert jobs[0].name == "Nightly report"
    assert total == 1


async def test_cron_save_fields_edits_then_rereads(
    executor: HermesExecutor, fake_hermes_cli
) -> None:
    store = build_store(executor, "").cron
    updated = await store.save_fields("aabbccddeeff", {"name": "Renamed job"})
    assert updated.name == "Renamed job"
    assert fake_hermes_cli.jobs_actions == [
        ("cron", "edit", "aabbccddeeff", "--name", "Renamed job")
    ]


async def test_kanban_list_tasks_filters_by_status(executor: HermesExecutor, fake_kanban) -> None:
    """`fake_kanban` is unused directly -- requesting it is what seeds
    `kanban.db` under the same `hermes_home` `executor` reads from."""
    store = build_store(executor, "").kanban
    tasks, total = await store.list_tasks(status="ready")
    assert [t.id for t in tasks] == ["t_1"]
    assert tasks[0].comment_count == 1
    assert total == 1

    tasks, total = await store.list_tasks(status="done")
    assert tasks == []
    assert total == 0

    tasks, total = await store.list_tasks()
    assert [t.id for t in tasks] == ["t_1"]
    assert total == 1


async def test_kanban_get_task_detail_includes_comments_and_runs(
    executor: HermesExecutor, fake_kanban
) -> None:
    detail = await build_store(executor, "").kanban.get_task_detail("t_1")
    assert detail.task.title == "Fix flaky test"
    assert [c.body for c in detail.comments] == ["Looking into it"]
    assert detail.runs[0]["status"] == "completed"
