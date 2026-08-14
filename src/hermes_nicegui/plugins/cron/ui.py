"""Cron plugin pages: job list + a per-job detail/edit view.

The detail page offers two ways to edit a job: a plain form over the core
fields (name, schedule, prompt, deliver, skills, repeat) that PATCHes just
those keys, and a raw-YAML tab (``ui.codemirror``) over the job's full record
for anything the form doesn't expose -- the gateway's ``PATCH /api/jobs/{id}``
already whitelists fields server-side, so posting the full parsed YAML back
is as safe as the form path.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import yaml
from nicegui import background_tasks, ui

from hermes_nicegui.gateway import HermesError, Job
from hermes_nicegui.plugin import Plugin
from hermes_nicegui.plugins.cron.logic import (
    fmt_iso,
    fmt_iso_age,
    fmt_repeat,
    job_to_yaml,
    skills_to_text,
    state_icon,
    text_to_skills,
    yaml_to_job_fields,
)
from hermes_nicegui.web import frame


def register_pages(plugin: Plugin) -> None:
    client = plugin.client
    logger = plugin.logger

    def _create_job_dialog(on_created: Any) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
            ui.label("New cron job").classes("text-lg font-bold")
            name = ui.input("Name").props("outlined dense").classes("w-full").mark("new-job-name")
            schedule = (
                ui.input(
                    "Schedule",
                    placeholder="every 5m, */5 * * * *, or an ISO timestamp",
                )
                .props("outlined dense")
                .classes("w-full")
                .mark("new-job-schedule")
            )
            prompt = ui.textarea("Prompt").props("outlined dense").classes("w-full")
            deliver = (
                ui.input("Deliver", placeholder="local")
                .props("outlined dense")
                .classes("w-full")
            )
            skills = (
                ui.input("Skills", placeholder="comma-separated")
                .props("outlined dense")
                .classes("w-full")
            )
            repeat = (
                ui.number("Repeat (blank = forever)", min=1)
                .props("outlined dense")
                .classes("w-full")
            )

            async def do_create() -> None:
                new_name = (name.value or "").strip()
                new_schedule = (schedule.value or "").strip()
                if not new_name or not new_schedule:
                    ui.notify("Name and schedule are required", type="warning")
                    return
                try:
                    job = await client.create_job(
                        name=new_name,
                        schedule=new_schedule,
                        prompt=prompt.value or "",
                        deliver=(deliver.value or "").strip() or None,
                        skills=text_to_skills(skills.value or ""),
                        repeat=int(repeat.value) if repeat.value else None,
                    )
                except HermesError as exc:
                    ui.notify(f"Create failed: {exc}", type="negative")
                    return
                dialog.close()
                ui.notify("Job created", type="positive")
                on_created(job)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Create", on_click=lambda: background_tasks.create(do_create())
                ).mark("create-job-confirm")
        dialog.open()

    @ui.page("/cron", title="Cron Jobs")
    async def cron_list_page() -> None:
        with frame(active="/cron"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Scheduled jobs").classes("text-lg")
                ui.space()
                ui.button(
                    icon="refresh", on_click=lambda: background_tasks.create(load_list())
                ).props("flat round dense").mark("refresh-button").tooltip("Refresh")
                ui.button(
                    "New job",
                    icon="add",
                    on_click=lambda: _create_job_dialog(
                        lambda _job: background_tasks.create(load_list())
                    ),
                ).mark("new-job-button")

            list_container = ui.list().props("separator").classes("w-full")

            def render_job_row(job: Job) -> None:
                icon, color = state_icon(job.state)
                with (
                    ui.item(on_click=partial(ui.navigate.to, f"/cron/{job.id}"))
                    .props("v-ripple")
                    .mark("job-row")
                ):
                    with ui.item_section().props("avatar"):
                        ui.icon(icon, color=color)
                    with ui.item_section():
                        ui.item_label(job.name or "(unnamed)")
                        ui.item_label(job.schedule_display).props("caption lines=1")
                    with ui.item_section().props("side top"):
                        ui.label(fmt_iso_age(job.next_run_at)).classes("text-xs opacity-60")
                        ui.badge(job.deliver or "local", color="grey")

            async def load_list() -> None:
                try:
                    jobs = await client.list_jobs()
                except HermesError as exc:
                    ui.notify(f"Failed to load jobs: {exc}", type="negative")
                    return
                list_container.clear()
                with list_container:
                    if not jobs:
                        ui.label("No cron jobs yet.")
                    for job in jobs:
                        render_job_row(job)

            await load_list()
            logger.debug("cron list rendered")

    def _pause_resume(job: Job, on_updated: Any) -> None:
        async def do_toggle() -> None:
            try:
                if job.enabled:
                    updated = await client.pause_job(job.id)
                else:
                    updated = await client.resume_job(job.id)
            except HermesError as exc:
                ui.notify(f"Failed: {exc}", type="negative")
                return
            ui.notify("Paused" if not updated.enabled else "Resumed", type="positive")
            on_updated(updated)

        background_tasks.create(do_toggle())

    def _run_now(job_id: str, on_updated: Any) -> None:
        async def do_run() -> None:
            try:
                updated = await client.run_job(job_id)
            except HermesError as exc:
                ui.notify(f"Run failed: {exc}", type="negative")
                return
            ui.notify("Job triggered", type="positive")
            on_updated(updated)

        background_tasks.create(do_run())

    def _delete_job(job_id: str, on_deleted: Any) -> None:
        async def do_delete() -> None:
            try:
                await client.delete_job(job_id)
            except HermesError as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")
                return
            ui.notify("Job deleted", type="positive")
            on_deleted()

        background_tasks.create(do_delete())

    def _save_fields(job_id: str, fields: dict[str, Any], on_updated: Any) -> None:
        async def do_save() -> None:
            try:
                updated = await client.update_job(job_id, fields)
            except HermesError as exc:
                ui.notify(f"Save failed: {exc}", type="negative")
                return
            ui.notify("Job saved", type="positive")
            on_updated(updated)

        background_tasks.create(do_save())

    @ui.page("/cron/{job_id}", title="Cron Job")
    async def cron_detail_page(job_id: str) -> None:
        with frame(active="/cron"):
            try:
                job = await client.get_job(job_id)
            except HermesError as exc:
                ui.label(f"Failed to load job: {exc}")
                return

            def stats_text(j: Job) -> str:
                return (
                    f"Next run {fmt_iso(j.next_run_at)} · "
                    f"Last run {fmt_iso(j.last_run_at)} ({j.last_status or 'never'}) · "
                    f"{fmt_repeat(j.repeat_times, j.repeat_completed)}"
                )

            with ui.card().classes("w-full"):
                with ui.row().classes("items-center"):
                    title_label = ui.label(job.name or "(unnamed)").classes("text-lg font-bold")
                    ui.space()
                    state_badge = ui.badge(job.state or "unknown", color=state_icon(job.state)[1])
                    ui.badge(job.deliver or "local", color="grey")
                stats_label = ui.label(stats_text(job))
                with ui.row():
                    pause_button = ui.button(
                        "Pause" if job.enabled else "Resume",
                        icon="pause" if job.enabled else "play_arrow",
                    ).mark("pause-resume-button")
                    run_button = ui.button("Run now", icon="play_circle").mark("run-now-button")
                    delete_button = ui.button(
                        "Delete", icon="delete", color="negative"
                    ).mark("delete-job-button")

            def apply_update(updated: Job) -> None:
                nonlocal job
                job = updated
                title_label.set_text(job.name or "(unnamed)")
                state_badge.set_text(job.state or "unknown")
                state_badge.set_background_color(state_icon(job.state)[1])
                stats_label.set_text(stats_text(job))
                pause_button.set_text("Pause" if job.enabled else "Resume")
                pause_button.set_icon("pause" if job.enabled else "play_arrow")
                yaml_editor.set_value(job_to_yaml(job.raw))

            pause_button.on_click(lambda: _pause_resume(job, apply_update))
            run_button.on_click(lambda: _run_now(job_id, apply_update))
            delete_button.on_click(
                lambda: _delete_job(job_id, lambda: ui.navigate.to("/cron"))
            )

            with ui.tabs().classes("w-full") as tabs:
                details_tab = ui.tab("details", label="Details")
                yaml_tab = ui.tab("yaml", label="Raw YAML")

            with ui.tab_panels(tabs, value=details_tab).classes("w-full"):
                with ui.tab_panel(details_tab):
                    name_input = (
                        ui.input("Name", value=job.name)
                        .props("outlined dense")
                        .classes("w-full")
                        .mark("job-name-input")
                    )
                    schedule_input = (
                        ui.input("Schedule", value=job.schedule_display)
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    prompt_input = (
                        ui.textarea("Prompt", value=job.prompt or "")
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    deliver_input = (
                        ui.input("Deliver", value=job.deliver or "")
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    skills_input = (
                        ui.input("Skills", value=skills_to_text(job.skills))
                        .props("outlined dense")
                        .classes("w-full")
                    )
                    repeat_input = (
                        ui.number("Repeat (blank = forever)", value=job.repeat_times, min=1)
                        .props("outlined dense")
                        .classes("w-full")
                    )

                    def save_details() -> None:
                        fields: dict[str, Any] = {
                            "name": name_input.value or "",
                            "schedule": schedule_input.value or "",
                            "prompt": prompt_input.value or "",
                            "deliver": deliver_input.value or "",
                            "skills": text_to_skills(skills_input.value or ""),
                            "repeat": int(repeat_input.value) if repeat_input.value else None,
                        }
                        _save_fields(job_id, fields, apply_update)

                    ui.button("Save", icon="save", on_click=save_details).mark(
                        "save-details-button"
                    )

                with ui.tab_panel(yaml_tab):
                    yaml_editor = ui.codemirror(
                        job_to_yaml(job.raw), language="YAML", theme="basicDark"
                    ).classes("w-full")

                    def save_yaml() -> None:
                        try:
                            fields = yaml_to_job_fields(yaml_editor.value)
                        except (yaml.YAMLError, ValueError) as exc:
                            ui.notify(f"Invalid YAML: {exc}", type="negative")
                            return
                        _save_fields(job_id, fields, apply_update)

                    ui.button("Save YAML", icon="save", on_click=save_yaml).mark(
                        "save-yaml-button"
                    )

            logger.debug("cron detail rendered for {}", job_id)
