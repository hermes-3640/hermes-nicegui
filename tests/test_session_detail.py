"""Tests for the session detail/transcript page and inline chat."""

from __future__ import annotations

import sqlite3

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
    await user.should_see("Delete")


async def test_send_message_streams_reply(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")

    user.find(marker="chat-input").type("What's the weather?")
    user.find(marker="chat-send").click()

    await user.should_see("What's the weather?")
    await user.should_see("Hello world")


async def test_send_message_refreshes_message_count(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("3 messages")

    user.find(marker="chat-input").type("another turn")
    user.find(marker="chat-send").click()

    await user.should_see("5 messages")


async def test_empty_session_shows_placeholder(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-2")
    await user.should_see("No messages yet.")


async def test_load_earlier_messages_paginates(
    user: User, context: PluginContext, fake_hermes_cli
) -> None:
    """Seed sess-1 past the default 100-message page so the detail page's
    "Load earlier" control has something to fetch, and its own oldest
    message (id 1, "How do I access your API?") starts out unloaded."""
    con = sqlite3.connect(fake_hermes_cli._state_db_path())
    con.executemany(
        "INSERT INTO messages (id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        [(i, "sess-1", "user", f"filler message {i}", 1786620000.0 + i) for i in range(4, 105)],
    )
    con.commit()
    con.close()

    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("filler message 104")
    await user.should_not_see("How do I access your API?")

    user.find(marker="load-earlier-button").click()
    await user.should_see("How do I access your API?")
