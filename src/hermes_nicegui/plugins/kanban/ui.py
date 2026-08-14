"""Kanban plugin pages: a filterable task list + a per-task detail/edit view.

The board is deliberately *not* laid out as columns-side-by-side (Trello
style): that pattern needs horizontal panning that doesn't work on a phone,
and there's no drag-and-drop here anyway (moving a task is a "Move to"
select, same on every screen size). Instead ``/kanban`` renders one vertical
list of every task with its status as a colored tag, plus a row of filter
tabs to narrow it down -- the same single-column list pattern the cron and
sessions pages already use.

Mirrors ``plugins/cron/ui.py``'s shape (closures per mutation, wrapped in
``background_tasks.create`` so they can be awaited from a synchronous
``on_click``, patch the already-rendered UI in place via an ``on_updated``
callback), with one difference: reads (`list_tasks`/task detail) go through
``web.current_store().kanban`` (direct SQLite against `kanban.db`), while
every mutation still goes through ``client`` (``KanbanClient``, the
dashboard's cookie-auth REST API) -- kanban's CLI (`hermes kanban ...`) is a
task-lifecycle tool with no generic field-setter, unlike cron's, so its
writes couldn't move the way cron's did. The one thing worth calling out:
the dashboard's ``POST /tasks``
always creates a task in the ``ready`` column, which the live dispatcher
picks up within ~60s and spawns a real agent run for -- there's no
``status`` field on create. The "New task" dialog defaults its own status
picker to the safer ``triage`` (parked, needs an explicit move) rather than
matching that server-side default, and issues a follow-up PATCH via
``KanbanClient.create_task``'s ``status=`` kwarg when the two differ.
"""

from __future__ import annotations

from functools import partial
from typing import Any, cast

from nicegui import background_tasks, ui

from hermes_nicegui import web
from hermes_nicegui.gateway import HermesError
from hermes_nicegui.pagination import Pager, render_pager
from hermes_nicegui.plugin import Plugin
from hermes_nicegui.plugins.kanban.gateway import Comment, KanbanError, Task
from hermes_nicegui.plugins.kanban.logic import (
    CANONICAL_COLUMNS,
    column_meta,
    fmt_epoch,
    fmt_epoch_age,
    fmt_priority,
)
from hermes_nicegui.web import frame


def register_pages(plugin: Plugin) -> None:
    client = plugin.kanban_client  # type: ignore[attr-defined]
    logger = plugin.logger

    def _create_task_dialog(on_created: Any) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("New task").classes("text-lg font-bold")
            title = (
                ui.input("Title").props("outlined dense").classes("w-full").mark("new-task-title")
            )
            body = ui.textarea("Description").props("outlined dense").classes("w-full")
            assignee = (
                ui.input("Assignee", value="default").props("outlined dense").classes("w-full")
            )
            priority = ui.number("Priority", value=2, min=0, max=5).props("outlined dense")
            status = (
                ui.select(CANONICAL_COLUMNS, value="triage", label="Initial status")
                .props("outlined dense")
                .classes("w-full")
                .mark("new-task-status")
            )
            ui.label(
                '"Ready" tasks are picked up by the live dispatcher within ~60s '
                'and spawn a real agent run. "Triage" stays parked until moved.'
            ).classes("text-xs opacity-60")

            async def do_create() -> None:
                new_title = (title.value or "").strip()
                if not new_title:
                    ui.notify("Title is required", type="warning")
                    return
                try:
                    task = await client.create_task(
                        title=new_title,
                        body=body.value or "",
                        assignee=(assignee.value or "default").strip() or "default",
                        priority=int(priority.value) if priority.value is not None else 2,
                        status=status.value,
                    )
                except KanbanError as exc:
                    ui.notify(f"Create failed: {exc}", type="negative")
                    return
                dialog.close()
                ui.notify("Task created", type="positive")
                on_created(task)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Create", on_click=lambda: background_tasks.create(do_create())).mark(
                    "create-task-confirm"
                )
        dialog.open()

    @ui.page("/kanban", title="Kanban")
    async def kanban_board_page() -> None:
        with frame(active="/kanban"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Kanban board").classes("text-lg")
                ui.space()
                ui.button(
                    icon="bolt", on_click=lambda: background_tasks.create(do_dispatch())
                ).props("flat round dense").mark("dispatch-button").tooltip(
                    "Trigger a dispatcher tick"
                )
                ui.button(
                    icon="refresh", on_click=lambda: background_tasks.create(_refresh())
                ).props("flat round dense").mark("refresh-button").tooltip("Refresh")
                ui.button(
                    "New task",
                    icon="add",
                    on_click=lambda: _create_task_dialog(
                        lambda _task: background_tasks.create(load_board())
                    ),
                ).mark("new-task-button")

            current_tasks: list[Task] = []
            total = 0
            pager = Pager()

            # `ui.tabs()`'s `value=` constructor kwarg is typed `Tab | TabPanel |
            # None` even though a plain tab-name string is documented and
            # supported at runtime -- set it as a plain attribute assignment
            # after construction instead, where it's typed against the wider
            # `ValueElement` generic that does include `str`.
            with ui.tabs().classes("w-full").mark("status-filter") as filter_tabs:
                ui.tab("all", label="All")
                for name in CANONICAL_COLUMNS:
                    label, icon, _color = column_meta(name)
                    ui.tab(name, label=label, icon=icon).mark(f"filter-tab-{name}")
            filter_tabs.value = "all"

            list_container = ui.list().props("separator").classes("w-full")

            def render_task_row(task: Task) -> None:
                label, icon, color = column_meta(task.status)
                with (
                    ui.item(on_click=partial(ui.navigate.to, f"/kanban/{task.id}"))
                    .props("v-ripple")
                    .mark("task-card")
                ):
                    with ui.item_section().props("avatar"):
                        ui.icon(icon, color=color)
                    with ui.item_section():
                        ui.item_label(task.title or "(untitled)")
                        ui.item_label(
                            f"{task.assignee or '—'} · {fmt_priority(task.priority)}"
                        ).props("caption lines=1")
                    with ui.item_section().props("side top"):
                        ui.badge(label, color=color)
                        if task.comment_count:
                            ui.badge(str(task.comment_count), color="grey")

            def render_list() -> None:
                list_container.clear()
                with list_container:
                    if not current_tasks:
                        ui.label("No tasks.").classes("text-sm opacity-60 q-pa-sm")
                    for task in current_tasks:
                        render_task_row(task)
                    render_pager(pager, total, lambda: background_tasks.create(load_board()))

            def _tab_changed(e: Any) -> None:
                pager.reset()
                background_tasks.create(load_board())

            filter_tabs.on_value_change(_tab_changed)

            async def do_dispatch() -> None:
                try:
                    await client.trigger_dispatch()
                except KanbanError as exc:
                    ui.notify(f"Dispatch failed: {exc}", type="negative")
                    return
                ui.notify("Dispatcher tick triggered", type="positive")
                await load_board()

            async def load_board() -> None:
                nonlocal total
                status_filter = cast(str, filter_tabs.value) or "all"
                try:
                    current_tasks[:], total = await web.current_store().kanban.list_tasks(
                        status=None if status_filter == "all" else status_filter,
                        limit=pager.limit,
                        offset=pager.offset,
                    )
                except HermesError as exc:
                    ui.notify(f"Failed to load board: {exc}", type="negative")
                    return
                render_list()

            async def _refresh() -> None:
                pager.reset()
                await load_board()

            await load_board()
            logger.debug("kanban board rendered")

    def _move_status(task_id: str, new_status: str, on_updated: Any) -> None:
        async def do_move() -> None:
            try:
                updated = await client.update_task(task_id, {"status": new_status})
            except KanbanError as exc:
                ui.notify(f"Move failed: {exc}", type="negative")
                return
            ui.notify(f"Moved to {column_meta(new_status)[0]}", type="positive")
            on_updated(updated)

        background_tasks.create(do_move())

    def _delete_task(task_id: str, on_deleted: Any) -> None:
        async def do_delete() -> None:
            try:
                await client.delete_task(task_id)
            except KanbanError as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")
                return
            ui.notify("Task deleted", type="positive")
            on_deleted()

        background_tasks.create(do_delete())

    def _save_fields(task_id: str, fields: dict[str, Any], on_updated: Any) -> None:
        async def do_save() -> None:
            try:
                updated = await client.update_task(task_id, fields)
            except KanbanError as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            ui.notify("Task saved", type="positive")
            on_updated(updated)

        background_tasks.create(do_save())

    def _add_comment(task_id: str, body: str, on_added: Any) -> None:
        async def do_add() -> None:
            if not body.strip():
                ui.notify("Comment can't be empty", type="warning")
                return
            try:
                await client.add_comment(task_id, body)
            except KanbanError as exc:
                ui.notify(f"Comment failed: {exc}", type="negative")
                return
            on_added()

        background_tasks.create(do_add())

    @ui.page("/kanban/{task_id}", title="Task")
    async def kanban_detail_page(task_id: str) -> None:
        with frame(active="/kanban"):
            try:
                detail = await web.current_store().kanban.get_task_detail(task_id)
            except HermesError as exc:
                ui.label(f"Failed to load task: {exc}")
                return
            task = detail.task

            def stats_text(t: Task) -> str:
                parts = [f"Created {fmt_epoch_age(t.created_at)}"]
                if t.started_at:
                    parts.append(f"started {fmt_epoch_age(t.started_at)}")
                if t.completed_at:
                    parts.append(f"completed {fmt_epoch_age(t.completed_at)}")
                if t.consecutive_failures:
                    parts.append(f"{t.consecutive_failures} consecutive failures")
                return " · ".join(parts)

            with ui.card().classes("w-full"):
                with ui.row().classes("items-center"):
                    title_label = ui.label(task.title or "(untitled)").classes("text-lg font-bold")
                    ui.space()
                    label, _icon, color = column_meta(task.status)
                    status_badge = ui.badge(label, color=color)
                stats_label = ui.label(stats_text(task)).classes("text-sm opacity-70")
                failure_label = ui.label(task.last_failure_error or "").classes(
                    "text-xs text-negative"
                )
                failure_label.set_visibility(bool(task.last_failure_error))

                with ui.row().classes("items-center gap-2"):
                    move_options = (
                        CANONICAL_COLUMNS
                        if task.status in CANONICAL_COLUMNS
                        else [*CANONICAL_COLUMNS, task.status]
                    )
                    move_select = (
                        ui.select(move_options, value=task.status, label="Move to")
                        .props("outlined dense")
                        .classes("w-48")
                        .mark("move-status-select")
                    )
                    delete_button = ui.button("Delete", icon="delete", color="negative").mark(
                        "delete-task-button"
                    )
                    if task.session_id:
                        ui.button(
                            "View session",
                            icon="terminal",
                            on_click=partial(ui.navigate.to, f"/sessions/{task.session_id}"),
                        ).props("outline").mark("view-session-button")

            if detail.runs:
                with ui.expansion(f"Runs ({len(detail.runs)})", icon="history").classes("w-full"):
                    for run in detail.runs:
                        run_id = run.get("id") or run.get("run_id") or "—"
                        run_status = run.get("status") or "unknown"
                        with ui.row().classes("items-center gap-2"):
                            ui.label(str(run_id)).classes("font-mono text-sm")
                            ui.badge(str(run_status), color="grey")
                            started = run.get("started_at")
                            if started:
                                ui.label(fmt_epoch_age(started)).classes("text-xs opacity-60")

            def apply_update(updated: Task) -> None:
                nonlocal task
                task = updated
                title_label.set_text(task.title or "(untitled)")
                label, _icon, color = column_meta(task.status)
                status_badge.set_text(label)
                status_badge.set_background_color(color)
                stats_label.set_text(stats_text(task))
                failure_label.set_text(task.last_failure_error or "")
                failure_label.set_visibility(bool(task.last_failure_error))
                move_select.set_value(task.status)

            def on_move_change(event: Any) -> None:
                if event.value and event.value != task.status:
                    _move_status(task_id, event.value, apply_update)

            move_select.on_value_change(on_move_change)
            delete_button.on_click(lambda: _delete_task(task_id, lambda: ui.navigate.to("/kanban")))

            title_input = (
                ui.input("Title", value=task.title)
                .props("outlined dense")
                .classes("w-full")
                .mark("task-title-input")
            )
            body_input = (
                ui.textarea("Description", value=task.body or "")
                .props("outlined dense")
                .classes("w-full")
                .mark("task-body-input")
            )
            with ui.row().classes("w-full gap-2"):
                assignee_input = (
                    ui.input("Assignee", value=task.assignee or "")
                    .props("outlined dense")
                    .classes("w-full")
                    .mark("task-assignee-input")
                )
                priority_input = (
                    ui.number("Priority", value=task.priority, min=0, max=5)
                    .props("outlined dense")
                    .classes("w-full")
                    .mark("task-priority-input")
                )

            def save_fields() -> None:
                fields: dict[str, Any] = {
                    "title": title_input.value or "",
                    "body": body_input.value or "",
                    "assignee": (assignee_input.value or "").strip() or "default",
                    "priority": (
                        int(priority_input.value) if priority_input.value is not None else 2
                    ),
                }
                _save_fields(task_id, fields, apply_update)

            ui.button("Save", icon="save", on_click=save_fields).mark("save-task-button")

            ui.label("Comments").classes("text-lg font-bold q-mt-md")
            comments_container = ui.column().classes("w-full")

            def render_comments(comments: list[Comment]) -> None:
                comments_container.clear()
                with comments_container:
                    if not comments:
                        ui.label("No comments yet.").classes("text-sm opacity-60")
                    for comment in comments:
                        with ui.card().classes("w-full"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(comment.author or "—").classes("font-bold text-sm")
                                ui.label(fmt_epoch(comment.created_at)).classes(
                                    "text-xs opacity-60"
                                )
                            ui.label(comment.body)

            render_comments(detail.comments)

            comment_input = (
                ui.textarea("Add a comment")
                .props("outlined dense")
                .classes("w-full")
                .mark("comment-input")
            )

            async def reload_comments() -> None:
                try:
                    fresh = await web.current_store().kanban.get_task_detail(task_id)
                except HermesError as exc:
                    ui.notify(f"Failed to refresh comments: {exc}", type="negative")
                    return
                comment_input.set_value("")
                render_comments(fresh.comments)

            ui.button(
                "Add comment",
                icon="add_comment",
                on_click=lambda: _add_comment(
                    task_id,
                    comment_input.value or "",
                    lambda: background_tasks.create(reload_comments()),
                ),
            ).mark("add-comment-button")

            logger.debug("kanban detail rendered for {}", task_id)
