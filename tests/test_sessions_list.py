"""Tests for the sessions list page: rendering, search, filtering, nav."""

from __future__ import annotations

from nicegui import ui
from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.sessions import SessionsPlugin


async def test_sessions_list_renders(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    await user.should_see("Cron run")
    await user.should_see("Unread")
    assert user.find(marker="refresh-button").elements


async def test_sessions_nav_item_present(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/")
    await user.should_see("Sessions")


async def test_rows_are_clickable(user: User, context: PluginContext) -> None:
    """Rows must carry Quasar's `clickable` prop for real-browser clicks."""
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    for item in user.find(marker="session-row").elements:
        assert item.props.get("clickable") is True


async def test_click_row_navigates_to_detail(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    user.find(marker="session-row").click()
    await user.should_see("How do I access your API?", retries=10)
    await user.should_see("Here is the answer.", retries=10)


async def test_unread_filter_toggles(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    toggle = user.find(ui.switch)
    toggle.click()
    await user.should_see("First session")


async def test_new_session_button_navigates_to_new_session(
    user: User, context: PluginContext
) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    user.find(marker="new-session-button").click()
    await user.should_see("Message Hermes", retries=10)
    assert user.find(marker="chat-input").elements


async def test_new_session_button_reuses_existing_blank_session(
    user: User, context: PluginContext, hermes
) -> None:
    """Clicking "New session" when a blank session already exists should
    reopen it instead of creating another one -- there should only ever be
    one blank session at a time."""
    hermes.sessions.append(
        {
            "id": "sess-blank",
            "title": None,
            "source": "webui",
            "message_count": 0,
            "last_active": 1786630000.0,
            "preview": None,
        }
    )
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    before = len(hermes.sessions)
    user.find(marker="new-session-button").click()
    await user.should_see("Message Hermes", retries=10)
    assert len(hermes.sessions) == before


async def test_search_filters_list(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions")
    await user.should_see("First session")
    await user.should_see("Cron run")
    search = user.find(kind=ui.input)
    search.type("cron")
    search.trigger("change")
    await user.should_see("Cron run", retries=10)
    await user.should_not_see("First session", retries=10)
