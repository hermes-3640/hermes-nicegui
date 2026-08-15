"""Tests for session source icons."""

from __future__ import annotations

from hermes_nicegui.plugins.sessions.logic import source_icon


def test_source_icons() -> None:
    assert source_icon("kanban") == "view_kanban"
    assert source_icon("webui") == "language"
    assert source_icon("api_server") == "api"
    assert source_icon("cron") == "schedule"


def test_unknown_source_uses_fallback_icon() -> None:
    assert source_icon("unknown") == "help_outline"
    assert source_icon(None) == "help_outline"
