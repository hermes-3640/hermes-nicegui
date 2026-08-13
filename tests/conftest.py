"""Shared pytest fixtures: fake Hermes gateway via httpx.MockTransport.

There is no ``main.py`` involved in a test (``main_file = ""`` in
``pyproject.toml``): a test builds a :class:`~hermes_nicegui.plugin.PluginContext`
via ``make_context``/``context``, constructs whichever plugin(s) it wants, and
calls ``hermes_nicegui.web.build(context, [...])`` itself. That keeps a test's
dependencies -- and which plugin it's actually exercising -- visible in the
test, instead of implicit in environment variables and entry-point discovery.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from loguru import logger

from hermes_nicegui.config import Settings
from hermes_nicegui.gateway import HermesClient
from hermes_nicegui.plugin import PluginContext


class FakeHermes:
    """In-memory stand-in for the Hermes API server."""

    def __init__(self) -> None:
        self.sessions: list[dict] = []
        self.messages: dict[str, list[dict]] = {}
        self.stream_events: list[tuple[str, dict]] = []
        self.reset()

    def reset(self) -> None:
        self.sessions = [
            {
                "id": "sess-1",
                "title": "First session",
                "source": "webui",
                "model": "deepseek-v4-flash",
                "message_count": 3,
                "tool_call_count": 1,
                "input_tokens": 100,
                "output_tokens": 50,
                "estimated_cost_usd": 0.001,
                "last_active": 1786620319.0,
                "preview": "hello",
            },
            {
                "id": "sess-2",
                "title": "Cron run",
                "source": "cron",
                "model": "deepseek-v4-flash",
                "message_count": 9,
                "tool_call_count": 4,
                "input_tokens": 900,
                "output_tokens": 300,
                "estimated_cost_usd": 0.01,
                "last_active": 1786610000.0,
                "preview": "nightly check",
            },
        ]
        self.messages = {
            "sess-1": [
                {
                    "id": 1,
                    "session_id": "sess-1",
                    "role": "user",
                    "content": "How do I access your API?",
                    "timestamp": 1786620000.0,
                },
                {
                    "id": 2,
                    "session_id": "sess-1",
                    "role": "assistant",
                    "content": "Here is the answer.",
                    "timestamp": 1786620010.0,
                },
                {
                    "id": 3,
                    "session_id": "sess-1",
                    "role": "assistant",
                    "content": "Used a tool to check.",
                    "timestamp": 1786620015.0,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "terminal",
                                "arguments": '{"command": "ls"}',
                            },
                        }
                    ],
                    "reasoning": "I needed to list files.",
                },
            ]
        }
        self.stream_events = [
            ("run.started", {"session_id": "sess-1", "run_id": "run_1"}),
            ("message.started", {"message": {"id": "msg_1", "role": "assistant"}}),
            ("tool.progress", {"tool_name": "_thinking", "delta": "thinking…"}),
            ("assistant.delta", {"delta": "Hello "}),
            ("assistant.delta", {"delta": "world"}),
            ("assistant.completed", {"content": "Hello world"}),
            ("run.completed", {"completed": True, "usage": {}}),
            ("done", {}),
        ]

    # -- handler -----------------------------------------------------------

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        if method == "GET" and path == "/api/sessions":
            return self._json({"object": "list", "data": self.sessions, "has_more": False})
        if method == "POST" and path == "/api/sessions":
            body = json.loads(request.content) if request.content else {}
            new_id = "sess-new"
            session = {
                "id": new_id,
                "title": body.get("title"),
                "source": "api_server",
                "model": "hermes-agent",
                "message_count": 0,
                "tool_call_count": 0,
                "estimated_cost_usd": None,
                "last_active": None,
                "preview": None,
            }
            self.sessions.append(session)
            return self._json({"object": "hermes.session", "session": session})
        if method == "GET" and path == "/api/sessions/sess-1":
            return self._json({"object": "hermes.session", "session": self.sessions[0]})
        if method == "GET" and path.startswith("/api/sessions/") and path.endswith("/messages"):
            sid = path.split("/")[3]
            return self._json({"object": "list", "data": self.messages.get(sid, [])})
        if method == "POST" and path == "/api/sessions/sess-1/model":
            return self._json({"model_lock": "accepted"})
        if method == "POST" and path == "/api/sessions/sess-1/fork":
            new_id = "sess-fork"
            return self._json(
                {"object": "hermes.session", "session": {"id": new_id, "title": "forked"}}
            )
        if method == "DELETE" and path == "/api/sessions/sess-1":
            return self._json({"deleted": True})
        if method == "POST" and path.endswith("/chat/stream"):
            return self._sse(self.stream_events)
        return self._json({"error": {"message": f"unhandled {method} {path}"}}, status=404)

    def _json(self, payload: dict, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=payload, headers={"Content-Type": "application/json"})

    def _sse(self, events: list[tuple[str, dict]]) -> httpx.Response:
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )


@pytest.fixture
def hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def make_context(hermes: FakeHermes) -> Callable[..., PluginContext]:
    """Factory for a :class:`PluginContext` wired to the fake gateway.

    A factory fixture (rather than a fixed ``context`` value) so tests that
    need a specific setting -- ``test_dark.py`` wants ``ui_dark=True``, say --
    can ask for it directly instead of reaching for env vars or markers.
    """

    def _make(**settings_kwargs: Any) -> PluginContext:
        settings = Settings(
            gateway_url="http://hermes.test",
            api_token="test-token",
            default_model="deepseek-v4-flash",
            **settings_kwargs,
        )
        client = HermesClient(
            settings.gateway_url,
            settings.api_token,
            transport=httpx.MockTransport(hermes.handle),
        )
        return PluginContext(client=client, settings=settings, logger=logger)

    return _make


@pytest.fixture
def context(make_context: Callable[..., PluginContext]) -> PluginContext:
    """The default fake context, for tests that don't need custom settings."""
    return make_context()
