"""Tests for the kanban task detail/edit page."""

from __future__ import annotations

from typing import cast

from nicegui import ui
from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.kanban import KanbanPlugin
from hermes_nicegui.plugins.kanban.gateway import KanbanClient

TASK_ID = "t_1"


async def test_detail_page_loads_directly(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Fix flaky test")


async def test_detail_page_shows_comments(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Looking into it")


async def test_detail_page_has_actions(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    assert user.find(marker="move-status-select").elements
    assert user.find(marker="delete-task-button").elements


async def test_move_status_updates_badge(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Fix flaky test")

    select = cast(ui.select, next(iter(user.find(marker="move-status-select").elements)))
    select.set_value("done")

    await user.should_see("Done", retries=10)


async def test_add_comment_appends_to_list(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Looking into it")

    user.find(marker="comment-input").type("On it now")
    user.find(marker="add-comment-button").click()

    await user.should_see("On it now", retries=10)


async def test_delete_navigates_to_board(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Fix flaky test")
    user.find(marker="delete-task-button").click()
    await user.should_see("Kanban board", retries=10)


async def test_view_session_button_shown_when_task_has_session(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Fix flaky test")
    assert user.find(marker="view-session-button").elements


async def test_runs_section_shows_run_status(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Runs (1)")
    await user.should_see("completed")


async def test_save_fields_updates_title(
    user: User, context: PluginContext, kanban_client: KanbanClient
) -> None:
    web.build(context, [KanbanPlugin(context, kanban_client=kanban_client)])
    await user.open(f"/kanban/{TASK_ID}")
    await user.should_see("Fix flaky test")

    title_input = user.find(marker="task-title-input")
    title_input.clear()
    title_input.type("Renamed task")
    user.find(marker="save-task-button").click()

    await user.should_see("Renamed task", retries=10)
