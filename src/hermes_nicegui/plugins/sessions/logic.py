"""Pure helpers for the sessions plugin: no NiceGUI, no I/O.

Formatting and filtering rules live here so they can be unit-tested directly
(see ``tests/test_unread.py``) without going through the NiceGUI ``user``
test harness. Anything that touches ``app.storage`` or the gateway belongs in
``ui.py`` instead.
"""

from __future__ import annotations

import json
from datetime import datetime

import yaml

from hermes_nicegui.gateway import Session


class _BlockStringDumper(yaml.SafeDumper):
    """A YAML dumper that renders multi-line strings as ``|`` block scalars.

    PyYAML's default style re-escapes embedded newlines as literal ``\\n``
    sequences -- the same unreadable form JSON is stuck with, since it never
    picks block style on its own. This is the standard representer override
    to make it do so, which is the entire reason for using YAML here.
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStringDumper.add_representer(str, _represent_str)


def _strip_trailing_whitespace(value: object) -> object:
    """Recursively rstrip each line of every string in a JSON-shaped value.

    PyYAML's literal block style (``|``) silently refuses to trigger if
    *any* line in the string has trailing whitespace -- common in real
    command output and markdown -- and falls back to an unreadable
    double-quoted, line-wrapped scalar instead. This is a display-only
    transform (it doesn't touch the underlying message data) that makes
    block style actually usable for realistic content.
    """
    if isinstance(value, str):
        return "\n".join(line.rstrip() for line in value.split("\n"))
    if isinstance(value, list):
        return [_strip_trailing_whitespace(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_trailing_whitespace(v) for k, v in value.items()}
    return value


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def fmt_age(ts: float | None) -> str:
    if not ts:
        return ""
    delta = datetime.now().timestamp() - ts
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return datetime.fromtimestamp(ts).strftime("%b %d")


def fmt_cost(cost: float | None) -> str:
    if cost is None:
        return ""
    return f"${cost:.4f}"


def is_unread(session: Session, seen: dict[str, float]) -> bool:
    """A session is unread if it has activity newer than the last time it was seen."""
    if not session.message_count or not session.last_active:
        return False
    return session.last_active > seen.get(session.id, 0.0)


_SOURCE_ICONS = {
    "webui": "language",
    "api_server": "api",
    "cron": "schedule",
}


def source_icon(source: str | None) -> str:
    """Material icon name for a session's source, replacing a two-letter badge."""
    return _SOURCE_ICONS.get(source or "", "help_outline")


def oneline(text: str, limit: int = 88) -> str:
    """First line of ``text``, trimmed to ``limit`` chars -- a collapsed row's summary."""
    first_line = text.strip().splitlines()[0] if text.strip() else "(empty)"
    return first_line if len(first_line) <= limit else first_line[: limit - 1] + "…"


def pretty_yaml(text: str) -> str:
    """Render ``text`` as YAML if it's valid JSON; otherwise return it unchanged.

    Tool call arguments and results arrive as compact single-line JSON --
    fine for the wire, unreadable in a code viewer with room to spare. YAML
    over re-indented JSON specifically because a JSON string can only ever
    hold a newline as a literal ``\\n`` escape, even pretty-printed; YAML's
    block-scalar style (see ``_BlockStringDumper``) renders an embedded
    multi-line value -- a command's stdout, say -- as actual lines. Non-JSON
    text (plain command output with no JSON wrapper) passes through as-is.
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    parsed = _strip_trailing_whitespace(parsed)
    return yaml.dump(parsed, Dumper=_BlockStringDumper, sort_keys=False, allow_unicode=True)
