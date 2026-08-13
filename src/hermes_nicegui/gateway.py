"""Async client for the Hermes Agent API server.

Thin wrapper around :class:`httpx.AsyncClient`. All methods are async; SSE
streams are consumed incrementally and never block the event loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from loguru import logger


class HermesError(Exception):
    """Raised when the gateway returns a non-2xx response."""


#: Injectable default transport used when tests need to fake the gateway
#: without touching network. Tests set this to ``httpx.MockTransport``.
DEFAULT_TRANSPORT: httpx.AsyncBaseTransport | None = None


@dataclass
class StreamEvent:
    """A single parsed SSE event from a Hermes stream endpoint."""

    event: str
    data: dict[str, Any]


@dataclass
class Session:
    """A Hermes session (from ``GET /api/sessions``)."""

    id: str
    title: str | None = None
    source: str | None = None
    model: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    end_reason: str | None = None
    message_count: int = 0
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    pinned: bool = False
    archived: bool = False
    last_active: float | None = None
    preview: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=data["id"],
            title=data.get("title"),
            source=data.get("source"),
            model=data.get("model"),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            end_reason=data.get("end_reason"),
            message_count=data.get("message_count", 0),
            tool_call_count=data.get("tool_call_count", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            estimated_cost_usd=data.get("estimated_cost_usd"),
            pinned=data.get("pinned", False),
            archived=data.get("archived", False),
            last_active=data.get("last_active"),
            preview=data.get("preview"),
        )


@dataclass
class Message:
    """A message in a session transcript."""

    id: int
    session_id: str
    role: str
    content: str | None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    timestamp: float | None = None
    finish_reason: str | None = None
    reasoning: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Message:
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            role=data["role"],
            content=data.get("content"),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
            tool_name=data.get("tool_name"),
            timestamp=data.get("timestamp"),
            finish_reason=data.get("finish_reason"),
            reasoning=data.get("reasoning"),
        )


class StreamEventSink(Protocol):
    """Callback interface for consuming :class:`StreamEvent` objects."""

    async def __call__(self, event: StreamEvent) -> None: ...


@dataclass
class _SSEBuffer:
    """Accumulates SSE ``event:`` / ``data:`` lines into events."""

    event_name: str = ""
    data_lines: list[str] = field(default_factory=list)

    def push(self, line: str) -> StreamEvent | None:
        """Feed one raw SSE line; returns a complete event when a blank line arrives."""
        line = line.rstrip("\r")
        if line == "":
            return self.flush()
        if line.startswith(":"):
            return None  # comment
        if line.startswith("event:"):
            self.event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            self.data_lines.append(line[len("data:") :].lstrip())
        return None

    def flush(self) -> StreamEvent | None:
        if not self.event_name:
            self.reset()
            return None
        payload = "\n".join(self.data_lines)
        data: dict[str, Any] = {}
        if payload:
            import json

            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"raw": payload}
        evt = StreamEvent(event=self.event_name, data=data)
        self.reset()
        return evt

    def reset(self) -> None:
        self.event_name = ""
        self.data_lines = []


class HermesClient:
    """Async client for the Hermes API server.

    ``transport`` is injectable so tests can swap in ``httpx.MockTransport``.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            transport=transport or DEFAULT_TRANSPORT,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )
        logger.debug("HermesClient -> {}", self.base_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        resp = await self._client.request(method, path, json=json, params=params)
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise HermesError(f"{method} {path} -> {resp.status_code}: {body}")
        return resp.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def capabilities(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/capabilities")

    async def list_models(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/models")
        return data.get("data", [])

    async def model_options(self, refresh: bool = False) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", "/api/model/options", params={"refresh": "1"} if refresh else None
        )
        return data.get("providers", [])

    async def list_skills(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/skills")
        return data.get("data", [])

    async def list_toolsets(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/v1/toolsets")
        return data.get("data", [])

    # -- sessions ---------------------------------------------------------

    async def list_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        source: str | None = None,
        include_children: bool = False,
    ) -> tuple[list[Session], bool]:
        """Return ``(sessions, has_more)`` for the given page."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "include_children": include_children,
        }
        if source:
            params["source"] = source
        data = await self._request("GET", "/api/sessions", params=params)
        sessions = [Session.from_json(s) for s in data.get("data", [])]
        return sessions, data.get("has_more", False)

    async def create_session(self, title: str | None = None) -> Session:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        data = await self._request("POST", "/api/sessions", json=body)
        inner = data.get("session") or data
        return Session.from_json(inner)

    async def get_session(self, session_id: str) -> Session:
        data = await self._request("GET", f"/api/sessions/{session_id}")
        inner = data.get("session") or data
        return Session.from_json(inner)

    async def update_session(
        self, session_id: str, *, title: str | None = None, end_reason: str | None = None
    ) -> Session:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if end_reason is not None:
            body["end_reason"] = end_reason
        data = await self._request("PATCH", f"/api/sessions/{session_id}", json=body)
        inner = data.get("session") or data
        return Session.from_json(inner)

    async def delete_session(self, session_id: str) -> bool:
        await self._request("DELETE", f"/api/sessions/{session_id}")
        return True

    async def fork_session(self, session_id: str, *, title: str | None = None) -> Session:
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        data = await self._request("POST", f"/api/sessions/{session_id}/fork", json=body)
        inner = data.get("session") or data
        return Session.from_json(inner)

    async def set_session_model(
        self, session_id: str, *, model: str, provider: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model}
        if provider:
            body["provider"] = provider
        return await self._request("POST", f"/api/sessions/{session_id}/model", json=body)

    async def session_messages(
        self, session_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[Message]:
        data = await self._request(
            "GET",
            f"/api/sessions/{session_id}/messages",
            params={"limit": limit, "offset": offset},
        )
        return [Message.from_json(m) for m in data.get("data", [])]

    # -- chat --------------------------------------------------------------

    async def stream_turn(
        self,
        session_id: str,
        input_text: str,
        *,
        model: str | None = None,
        provider: str | None = None,
        sink: StreamEventSink | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run one agent turn against a session, yielding SSE events.

        Events arrive incrementally; the iterator never buffers the whole
        stream, so the event loop stays responsive.
        """
        body: dict[str, Any] = {"input": input_text}
        if model:
            body["model"] = model
        if provider:
            body["provider"] = provider

        buffer = _SSEBuffer()
        async with self._client.stream(
            "POST", f"/api/sessions/{session_id}/chat/stream", json=body
        ) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode("utf-8", "replace")[:500]
                path = f"/api/sessions/{session_id}/chat/stream"
                raise HermesError(f"POST {path} -> {resp.status_code}: {text}")
            async for line in resp.aiter_lines():
                event = buffer.push(line)
                if event is not None:
                    if sink is not None:
                        await sink(event)
                    yield event

    async def chat_turn(
        self,
        session_id: str,
        input_text: str,
        *,
        model: str | None = None,
        provider: str | None = None,
    ) -> str:
        """Synchronous (non-streaming) single turn; returns final assistant text."""
        events = [
            e
            async for e in self.stream_turn(session_id, input_text, model=model, provider=provider)
        ]
        for event in reversed(events):
            if event.event == "assistant.completed":
                return event.data.get("content", "")
        return ""
