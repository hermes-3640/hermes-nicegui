"""Tests for the kanban board page: rendering, nav, filters, create dialog."""

from __future__ import annotations

from typing import cast

from nicegui import ui
from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.kanban import KanbanPlugin
from hermes_nicegui.plugins.kanban.gateway import KanbanClient


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
