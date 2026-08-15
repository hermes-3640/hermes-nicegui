"""Tests for the kanban board page: rendering, nav, filters, create dialog."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import cast

from nicegui import ui
from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.kanban import KanbanPlugin
from hermes_nicegui.plugins.kanban.gateway import KanbanClient
from tests.conftest import FakeHermesCli, FakeKanban


async def test_kanban_board_renders(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")
    assert user.find(marker="new-task-button").elements


async def test_kanban_nav_item_present(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/")
    await user.should_see("Kanban")


async def test_task_cards_are_clickable(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    """Cards must carry Quasar's `clickable` prop for real-browser clicks."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")
    for item in user.find(marker="task-card").elements:
        assert item.props.get("clickable") is True


async def test_click_card_navigates_to_detail(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")
    user.find(marker="task-card").click()
    await user.should_see("Comments", retries=10)


async def test_filter_tab_hides_tasks_in_other_status(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    """``t_1`` is ``ready``; switching to the ``done`` filter should hide it."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    tabs = cast(ui.tabs, next(iter(user.find(marker="status-filter").elements)))
    tabs.set_value("done")

    await user.should_see("No tasks.", retries=10)
    await user.should_not_see("Fix flaky test", retries=10)


async def test_filter_tabs_order_review_before_blocked(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    """The status filter tabs must render in canonical order -- ``review``
    before ``blocked`` (ticket: "review should be before blocked in kanban
    views")."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    tabs = cast(ui.tabs, next(iter(user.find(marker="status-filter").elements)))
    names = [tab.props["name"] for tab in tabs.default_slot.children]
    assert names.index("review") < names.index("blocked")


async def test_create_task_dialog_adds_card(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    user.find(marker="new-task-button").click()
    await user.should_see("New task", retries=10)
    user.find(marker="new-task-title").type("Write docs")
    user.find(marker="create-task-confirm").click()

    await user.should_see("Write docs", retries=10)


async def test_board_paginates_with_numbered_pages(
    user: User, context: PluginContext, kanban_client: KanbanClient, fake_kanban
) -> None:
    for i in range(35):
        fake_kanban.insert_task(
            {
                "id": f"t-extra-{i}",
                "title": f"Extra task {i}",
                "body": "",
                "assignee": "default",
                "status": "ready",
                "priority": 1,
                "tenant": None,
                "created_at": 1786620000 + i,
                "started_at": None,
                "completed_at": None,
                "consecutive_failures": 0,
                "last_failure_error": None,
                "current_run_id": None,
                "session_id": None,
            }
        )
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")
    assert len(user.find(marker="task-card").elements) == 30

    pager = cast(ui.pagination, next(iter(user.find(marker="page-control").elements)))
    pager.set_value(2)
    await user.should_see("Extra task 29", retries=10)
    assert len(user.find(marker="task-card").elements) == 6
    await user.should_not_see("Fix flaky test", retries=10)


async def test_search_filters_board(
    user: User,
    context: PluginContext,
    kanban_client: KanbanClient,
    fake_kanban: FakeKanban,
) -> None:
    """Typing into the kanban-search box narrows the board to matching tasks
    (title/body/assignee/id/comments) and shows "No tasks." on no match --
    the same `LIKE` search the sessions list page uses."""
    fake_kanban.insert_task(
        {
            "id": "t_cleanup",
            "title": "Clean up backlog",
            "body": "",
            "assignee": "default",
            "status": "ready",
            "priority": 1,
            "tenant": None,
            "created_at": 1786620001,
            "started_at": None,
            "completed_at": None,
            "consecutive_failures": 0,
            "last_failure_error": None,
            "current_run_id": None,
            "session_id": None,
        }
    )
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")
    await user.should_see("Clean up backlog")

    search = user.find(marker="kanban-search")
    search.type("flaky")
    search.trigger("change")
    await user.should_see("Fix flaky test", retries=10)
    await user.should_not_see("Clean up backlog", retries=10)

    search.clear()
    search.type("zzz-nonexistent")
    search.trigger("change")
    await user.should_see("No tasks.", retries=10)
    await user.should_not_see("Fix flaky test", retries=10)


async def test_search_combines_with_status_tab(
    user: User,
    context: PluginContext,
    kanban_client: KanbanClient,
    fake_kanban: FakeKanban,
) -> None:
    """Search and the status tabs combine: a query whose only match is a
    `done` task shows nothing while the `ready` tab is selected."""
    fake_kanban.insert_task(
        {
            "id": "t_legacy",
            "title": "Retire the legacy flaky report",
            "body": "",
            "assignee": "default",
            "status": "done",
            "priority": 1,
            "tenant": None,
            "created_at": 1786620002,
            "started_at": None,
            "completed_at": 1786620300,
            "consecutive_failures": 0,
            "last_failure_error": None,
            "current_run_id": None,
            "session_id": None,
        }
    )
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    search = user.find(marker="kanban-search")
    search.type("legacy")
    search.trigger("change")
    await user.should_see("Retire the legacy flaky report", retries=10)
    await user.should_not_see("Fix flaky test", retries=10)

    tabs = cast(ui.tabs, next(iter(user.find(marker="status-filter").elements)))
    tabs.set_value("ready")

    await user.should_see("No tasks.", retries=10)
    await user.should_not_see("Retire the legacy flaky report", retries=10)


def _task_row(db_path: str, task_id: str) -> tuple[str, str]:
    """(status, body) of a task directly from the fake kanban.db — the same
    file the board page re-reads after an auto-specify, mirroring how the
    real `hermes kanban specify` CLI mutates the daemon's DB in place."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT status, body FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, f"task {task_id} not in kanban.db"
    return row[0], row[1]


async def test_create_task_empty_body_triggers_auto_specify(
    user: User,
    context: PluginContext,
    kanban_client: KanbanClient,
    fake_hermes_cli: FakeHermesCli,
    fake_kanban: FakeKanban,
) -> None:
    """An empty-description task created at the dialog's default Triage
    status gets auto-specified in the background: the UI shells out to
    `hermes kanban specify <id> --json` and the task lands with a fleshed-out
    body, promoted triage -> todo."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    user.find(marker="new-task-button").click()
    await user.should_see("New task", retries=10)
    user.find(marker="new-task-title").type("Write docs")
    user.find(marker="create-task-confirm").click()

    # Let the background auto-specify (fake CLI) land, then check it ran and
    # wrote through to kanban.db.
    for _ in range(50):
        if fake_hermes_cli.kanban_actions:
            break
        await asyncio.sleep(0.05)
    assert fake_hermes_cli.kanban_actions, "auto-specify was never triggered"
    args = fake_hermes_cli.kanban_actions[0]
    assert args[:2] == ("kanban", "specify")
    assert args[-1] == "--json"

    status, body = _task_row(str(fake_kanban.db_path), "t_2")
    assert status == "todo"
    assert body.startswith("**Goal**")


async def test_create_task_with_body_skips_auto_specify(
    user: User,
    context: PluginContext,
    kanban_client: KanbanClient,
    fake_hermes_cli: FakeHermesCli,
) -> None:
    """A task created with a real description must NOT be auto-specified —
    the specifier is only for cards that arrive with an empty body."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    user.find(marker="new-task-button").click()
    await user.should_see("New task", retries=10)
    user.find(marker="new-task-title").type("Write docs")
    user.find(marker="new-task-body").type("Document the API and the CLI.")
    user.find(marker="create-task-confirm").click()
    await user.should_see("Write docs", retries=10)
    await asyncio.sleep(0.2)

    assert not fake_hermes_cli.kanban_actions, "auto-specify ran for a task with a body"


async def test_create_task_empty_body_non_triage_warns(
    user: User,
    context: PluginContext,
    kanban_client: KanbanClient,
    fake_hermes_cli: FakeHermesCli,
) -> None:
    """An empty-body task created at a non-Triage status can't use the
    triage specifier (it refuses), so no specify call is made — the dialog
    warns instead."""
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open("/kanban")
    await user.should_see("Fix flaky test")

    user.find(marker="new-task-button").click()
    await user.should_see("New task", retries=10)
    user.find(marker="new-task-title").type("Write docs")
    status_select = cast(
        ui.select, next(iter(user.find(marker="new-task-status").elements))
    )
    status_select.set_value("ready")
    user.find(marker="create-task-confirm").click()
    await user.should_see("Write docs", retries=10)
    await asyncio.sleep(0.2)

    assert not fake_hermes_cli.kanban_actions, "auto-specify ran for a non-triage task"
