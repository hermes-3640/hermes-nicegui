"""Pure helpers for the cron plugin: no NiceGUI, no I/O.

Formatting, state-to-icon mapping and raw-YAML (de)serialization live here so
they're unit-testable directly, mirroring ``plugins/sessions/logic.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yaml


def fmt_iso(value: str | None) -> str:
    """Human-readable local time for an ISO-8601 timestamp string."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def fmt_iso_age(value: str | None) -> str:
    """A short relative label (``in 5m``, ``2h ago``) for an ISO-8601 timestamp."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = (dt - datetime.now(UTC)).total_seconds()
    future = delta >= 0
    delta = abs(delta)
    if delta < 60:
        return "now"
    if delta < 3600:
        label = f"{int(delta // 60)}m"
    elif delta < 86400:
        label = f"{int(delta // 3600)}h"
    else:
        label = dt.astimezone().strftime("%b %d")
    return f"in {label}" if future else f"{label} ago"


def fmt_repeat(times: int | None, completed: int) -> str:
    if times is None:
        return f"{completed} runs so far (repeats forever)"
    return f"{completed}/{times} runs"


# Operator-facing job states, per ``cron/jobs.py::effective_job_state`` in the
# Hermes Agent gateway: "scheduled" (normal), "paused", "running", plus the
# terminal "completed"/"error" states a finished repeat count can leave a job
# in.
_STATE_ICONS: dict[str, tuple[str, str]] = {
    "scheduled": ("schedule", "primary"),
    "paused": ("pause_circle", "grey"),
    "running": ("autorenew", "secondary"),
    "completed": ("check_circle", "positive"),
    "error": ("error", "negative"),
}


def state_icon(state: str | None) -> tuple[str, str]:
    """(icon, color) for a job's operator-facing state."""
    return _STATE_ICONS.get(state or "", ("help_outline", "grey"))


def skills_to_text(skills: list[str] | None) -> str:
    return ", ".join(skills or [])


def text_to_skills(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def job_to_yaml(job: dict[str, Any]) -> str:
    """Render a job's raw record as editable YAML."""
    return yaml.dump(job, sort_keys=False, allow_unicode=True)


def yaml_to_job_fields(text: str) -> dict[str, Any]:
    """Parse a raw-YAML edit back into a job-shaped dict.

    Raises ``yaml.YAMLError`` on invalid YAML, ``ValueError`` if the result
    isn't a mapping -- both are the caller's job to turn into a UI-facing
    error rather than a crash.
    """
    parsed = yaml.safe_load(text) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Job YAML must be a mapping")
    return parsed
