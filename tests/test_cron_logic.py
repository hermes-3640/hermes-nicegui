"""Tests for the cron plugin's pure helpers."""

from __future__ import annotations

import pytest

from hermes_nicegui.plugins.cron.logic import (
    fmt_iso,
    fmt_repeat,
    job_to_yaml,
    skills_to_text,
    state_icon,
    text_to_skills,
    yaml_to_job_fields,
)


def test_fmt_iso_missing() -> None:
    assert fmt_iso(None) == "—"


def test_fmt_iso_formats_known_value() -> None:
    assert fmt_iso("2026-08-13T12:00:00+00:00") != "—"


def test_fmt_iso_passes_through_unparseable_text() -> None:
    assert fmt_iso("not a date") == "not a date"


def test_fmt_repeat_forever() -> None:
    assert fmt_repeat(None, 3) == "3 runs so far (repeats forever)"


def test_fmt_repeat_bounded() -> None:
    assert fmt_repeat(5, 3) == "3/5 runs"


def test_state_icon_known() -> None:
    icon, color = state_icon("paused")
    assert icon == "pause_circle"
    assert color == "grey"


def test_state_icon_unknown_falls_back() -> None:
    icon, _color = state_icon(None)
    assert icon == "help_outline"


def test_skills_roundtrip() -> None:
    assert text_to_skills(skills_to_text(["a", "b"])) == ["a", "b"]


def test_text_to_skills_strips_blanks() -> None:
    assert text_to_skills(" a ,, b ,") == ["a", "b"]


def test_job_to_yaml_roundtrips_through_parser() -> None:
    job = {"id": "abc", "name": "test", "enabled": True}
    assert yaml_to_job_fields(job_to_yaml(job)) == job


def test_yaml_to_job_fields_rejects_non_mapping() -> None:
    with pytest.raises(ValueError):
        yaml_to_job_fields("- 1\n- 2\n")
