"""Async client for the Hermes dashboard's kanban plugin API.

This is a *different* backend than :class:`hermes_nicegui.gateway.HermesClient`:
the kanban board lives on the Hermes CLI's own dashboard web server (cookie
session auth via password login), not the gateway's ``/api/*`` surface every
other plugin here talks to. It runs as its own process/port, potentially on a
different host, so it gets its own client scoped to this plugin package
rather than something threaded through ``PluginContext``.
"""

from __future__ import annotations

from typing import Any

import httpx

from hermes_nicegui.dashboard_auth import DashboardError, DashboardSession
from hermes_nicegui.gateway import Board, Comment, Task, TaskDetail

KanbanError = DashboardError

__all__ = ["Board", "Comment", "KanbanClient", "KanbanError", "Task", "TaskDetail"]


class KanbanClient:
    """Async client for the Hermes dashboard's kanban plugin API.

    Auth is a session cookie (``DashboardSession``), not the bearer token
    the main gateway client uses.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._session = DashboardSession(
            base_url, username, password, transport=transport, timeout=timeout
        )
        self.base_url = self._session.base_url

    async def aclose(self) -> None:
        await self._session.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        return await self._session.request(
            method, f"/api/plugins/kanban{path}", json=json, params=params
        )

    async def get_board(self) -> Board:
        data = await self._request("GET", "/board")
        return Board.from_json(data)

    async def get_stats(self) -> dict[str, Any]:
        return await self._request("GET", "/stats")

    async def create_task(
        self,
        *,
        title: str,
        body: str = "",
        assignee: str = "default",
        priority: int = 2,
        status: str | None = None,
        skills: list[str] | None = None,
    ) -> Task:
        """Create a task. The API always creates tasks as ``ready`` -- there's
        no ``status`` field on create -- so if the caller wants a different
        starting status (e.g. the safer ``triage``, which the live dispatcher
        won't pick up), this issues a follow-up PATCH.
        """
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "assignee": assignee,
            "priority": priority,
        }
        if skills:
            payload["skills"] = skills
        data = await self._request("POST", "/tasks", json=payload)
        task = Task.from_json(data["task"])
        if status and status != task.status:
            task = await self.update_task(task.id, {"status": status})
        return task

    async def get_task(self, task_id: str) -> TaskDetail:
        data = await self._request("GET", f"/tasks/{task_id}")
        return TaskDetail.from_json(data)

    async def update_task(self, task_id: str, fields: dict[str, Any]) -> Task:
        data = await self._request("PATCH", f"/tasks/{task_id}", json=fields)
        return Task.from_json(data["task"])

    async def delete_task(self, task_id: str) -> bool:
        await self._request("DELETE", f"/tasks/{task_id}")
        return True

    async def add_comment(self, task_id: str, body: str) -> None:
        await self._request("POST", f"/tasks/{task_id}/comments", json={"body": body})
