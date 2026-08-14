"""Tests for the session detail/transcript page and the chat (pty) page."""

from __future__ import annotations

from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.sessions import SessionsPlugin


async def test_detail_page_loads_directly(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")


async def test_detail_page_shows_all_messages(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")
    await user.should_see("Here is the answer.")


async def test_detail_page_has_actions(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("Rename")
    await user.should_see("Continue in chat")
    await user.should_see("Delete")


async def test_continue_in_chat_navigates_to_resume_page(
    user: User, context: PluginContext
) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")
    user.find("Continue in chat").click()
    await user.should_see("Chat", retries=10)
    assert user.find(marker="chat-terminal").elements


async def test_empty_session_shows_placeholder(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-2")
    await user.should_see("No messages yet.")
