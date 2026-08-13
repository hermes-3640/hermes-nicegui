"""Tests for the email-inbox unread indicator."""

from __future__ import annotations

from hermes_nicegui.gateway import Session
from hermes_nicegui.plugins.sessions.logic import is_unread


def test_unread_logic() -> None:
    s = Session(id="x", message_count=5, last_active=2000.0)
    assert is_unread(s, {}) is True
    assert is_unread(s, {"x": 1999.0}) is True
    assert is_unread(s, {"x": 2000.0}) is False


def test_empty_session_not_unread() -> None:
    s = Session(id="x", message_count=0, last_active=2000.0)
    assert is_unread(s, {}) is False


def test_no_activity_not_unread() -> None:
    s = Session(id="x", message_count=5, last_active=None)
    assert is_unread(s, {}) is False
