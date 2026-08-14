"""Tests for the cron job list page: rendering, nav, create dialog."""

from __future__ import annotations

from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.cron import CronPlugin


async def test_cron_list_renders(user: User, context: PluginContext) -> None:
    web.build(context, [CronPlugin(context)])
    await user.open("/cron")
    await user.should_see("Nightly report")
    assert user.find(marker="new-job-button").elements


async def test_cron_nav_item_present(user: User, context: PluginContext) -> None:
    web.build(context, [CronPlugin(context)])
    await user.open("/")
    await user.should_see("Cron Jobs")


async def test_rows_are_clickable(user: User, context: PluginContext) -> None:
    """Rows must carry Quasar's `clickable` prop for real-browser clicks."""
    web.build(context, [CronPlugin(context)])
    await user.open("/cron")
    await user.should_see("Nightly report")
    for item in user.find(marker="job-row").elements:
        assert item.props.get("clickable") is True


async def test_click_row_navigates_to_detail(user: User, context: PluginContext) -> None:
    web.build(context, [CronPlugin(context)])
    await user.open("/cron")
    await user.should_see("Nightly report")
    user.find(marker="job-row").click()
    await user.should_see("Details", retries=10)
    await user.should_see("Raw YAML", retries=10)


async def test_create_job_dialog_adds_row(user: User, context: PluginContext) -> None:
    web.build(context, [CronPlugin(context)])
    await user.open("/cron")
    await user.should_see("Nightly report")

    user.find(marker="new-job-button").click()
    await user.should_see("New cron job", retries=10)
    user.find(marker="new-job-name").type("Log rotation")
    user.find(marker="new-job-schedule").type("every 1h")
    user.find(marker="create-job-confirm").click()

    await user.should_see("Log rotation", retries=10)
