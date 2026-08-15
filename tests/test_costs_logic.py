"""Tests for costs provider logic."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from hermes_nicegui.config import Settings
from hermes_nicegui.plugins.costs.logic import (
    OpenCodeGoProvider,
    OpenCodeLocalProvider,
    OpenRouterProvider,
    build_providers,
)


def _client(responses: dict[str, dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[str(request.url)])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openrouter_summary_uses_periods() -> None:
    client = _client(
        {
            "https://openrouter.ai/api/v1/key": {
                "data": {
                    "usage": 999.0,
                    "usage_monthly": 106.75,
                    "usage_weekly": 25.5,
                    "usage_daily": 4.25,
                }
            }
        }
    )
    provider = OpenRouterProvider(env_file="/missing", client=client)
    provider.key = "sk-or-v1-0123456789abcdef0123456789abcdef"
    provider.available = True
    summary = await provider.summary()
    assert summary.used == 106.75
    assert summary.total is None
    assert summary.remaining is None
    assert [(window.label, window.used) for window in summary.windows] == [
        ("This month", 106.75),
        ("This week", 25.5),
        ("Today", 4.25),
    ]
    assert len(summary.windows) == 3
    assert summary.currency == "USD"
    await client.aclose()


async def test_openrouter_usage_masks_key() -> None:
    key = "sk-or-v1-0123456789abcdef0123456789abcdef"
    client = _client(
        {
            "https://openrouter.ai/api/v1/key": {
                "data": {
                    "usage": 1,
                    "usage_daily": 2,
                    "usage_weekly": 3,
                    "usage_monthly": 4,
                }
            }
        }
    )
    provider = OpenRouterProvider(env_file="/missing", client=client)
    provider.key = key
    provider.available = True
    entries = await provider.usage()
    assert len(entries) == 3
    assert [entry.cost for entry in entries] == [4, 3, 2]
    assert "…" in entries[0].label
    assert key not in entries[0].label
    await client.aclose()


def test_openrouter_key_resolution(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing"
    assert not OpenRouterProvider(missing).available
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert OpenRouterProvider(missing).available


async def test_opencode_provider(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    ref = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def timestamp(age: timedelta) -> int:
        return int((ref - age).timestamp() * 1000)

    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE session (title TEXT, model TEXT, cost REAL, tokens_input INTEGER, "
            "tokens_output INTEGER, time_created INTEGER)"
        )
        connection.executemany(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "recent Go",
                    '{"id":"gpt-5.6-luna","providerID":"opencode-go","variant":"default"}',
                    2.5,
                    30,
                    40,
                    timestamp(timedelta(minutes=30)),
                ),
                (
                    "week Go",
                    '{"id":"gpt-5.6-luna","providerID":"opencode-go"}',
                    3.5,
                    10,
                    20,
                    timestamp(timedelta(days=3)),
                ),
                (
                    "month Go",
                    '{"id":"gpt-5.6-luna","providerID":"opencode-go"}',
                    4.0,
                    10,
                    20,
                    timestamp(timedelta(days=10)),
                ),
                (
                    "other provider",
                    '{"id":"other","providerID":"openrouter"}',
                    100.0,
                    10,
                    20,
                    timestamp(timedelta(minutes=15)),
                ),
            ],
        )
    provider = OpenCodeLocalProvider(str(db), monthly_limit=20, weekly_limit=10, five_hour_limit=5)
    summary = await provider.summary(now=ref)
    assert summary.used == 10
    assert summary.total == 20
    assert summary.remaining == 10
    assert summary.note == "Estimated from local session records — console usage may differ"
    assert [(window.used, window.remaining) for window in summary.windows] == [
        (10, 10),
        (6, 4),
        (2.5, 2.5),
    ]
    entries = await provider.usage()
    assert [entry.label for entry in entries] == [
        "other provider",
        "recent Go",
        "week Go",
        "month Go",
    ]
    assert entries[0].model == "other"
    assert entries[1].model == "gpt-5.6-luna"
    assert entries[0].when is not None and entries[0].when.tzinfo == UTC
    assert not OpenCodeLocalProvider(str(tmp_path / "missing.db")).available


def test_build_providers() -> None:
    providers = build_providers(Settings())
    assert isinstance(providers[0], OpenRouterProvider)
    assert isinstance(providers[1], OpenCodeGoProvider)


async def test_opencode_go_provider_uses_server_usage() -> None:
    client = _client(
        {
            "https://opencode.ai/zen/go/v1/usage": {
                "usage": {
                    "rolling": {
                        "status": "ok",
                        "percent": 0,
                        "resetsAt": "2026-08-15T20:50:16.697Z",
                    },
                    "weekly": {
                        "status": "ok",
                        "percent": 41,
                        "resetsAt": "2026-08-17T00:00:00.697Z",
                    },
                    "monthly": {
                        "status": "ok",
                        "percent": 23,
                        "resetsAt": "2026-09-09T15:08:49.697Z",
                    },
                }
            }
        }
    )
    provider = OpenCodeGoProvider(
        env_file="/missing", client=client, monthly_limit=60, weekly_limit=30, five_hour_limit=12
    )
    provider.key = "test-key"
    provider.available = True
    summary = await provider.summary()
    assert summary.used == 13.8
    assert summary.total == 60
    assert summary.remaining == 46.2
    assert summary.note == "Server-reported OpenCode Go usage"
    assert [
        (window.label, window.used, window.limit, window.remaining) for window in summary.windows
    ] == [
        ("5-hour window", 0.0, 12.0, 12.0),
        ("This week", 12.3, 30.0, 17.7),
        ("This month", 13.8, 60.0, 46.2),
    ]
    assert all(window.resets_at is not None for window in summary.windows)
    assert isinstance(summary.windows[-1].resets_at, datetime)
    assert not hasattr(summary, "all_time")
    assert all(not window.label.lower().startswith("all time") for window in summary.windows)
    await client.aclose()


async def test_opencode_go_provider_falls_back_to_local(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    ref = datetime.now(UTC)

    def timestamp(age: timedelta) -> int:
        return int((ref - age).timestamp() * 1000)

    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE session (title TEXT, model TEXT, cost REAL, tokens_input INTEGER, "
            "tokens_output INTEGER, time_created INTEGER)"
        )
        connection.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?)",
            (
                "recent Go",
                '{"id":"gpt-5.6-luna","providerID":"opencode-go","variant":"default"}',
                2.5,
                30,
                40,
                timestamp(timedelta(seconds=30)),
            ),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenCodeGoProvider(
        env_file="/missing", client=client, monthly_limit=60, weekly_limit=30, five_hour_limit=12
    )
    provider.key = "test-key"
    provider.available = True
    provider.local_db = str(db)
    summary = await provider.summary()
    assert summary.note.startswith("Server usage unavailable")
    assert summary.degraded is True
    assert summary.used == 2.5
    await client.aclose()


async def test_opencode_go_provider_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    provider = OpenCodeGoProvider(env_file="/missing", local_db=str(tmp_path / "missing.db"))
    assert not provider.available
