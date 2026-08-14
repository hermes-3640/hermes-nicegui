"""Kanban plugin: view and manage tasks on the Hermes dashboard's kanban board.

Unlike the other plugins here, this one does **not** use ``context.client``
(the main gateway's `HermesClient`) -- the kanban board lives on a different
server, the Hermes CLI's own dashboard web server (cookie/password auth), so
the plugin builds and owns its own
:class:`~hermes_nicegui.plugins.kanban.gateway.KanbanClient` instead.
"""

from __future__ import annotations

from hermes_nicegui.plugin import Plugin, PluginContext

from .gateway import KanbanClient
from .ui import register_pages


class KanbanPlugin(Plugin):
    name = "kanban"
    title = "Kanban"
    icon = "view_kanban"
    route = "/kanban"

    def __init__(
        self, context: PluginContext, *, kanban_client: KanbanClient | None = None
    ) -> None:
        super().__init__(context)
        self.kanban_client = kanban_client or KanbanClient(
            context.settings.kanban_url,
            context.settings.kanban_username,
            context.settings.kanban_password,
        )

    def register(self) -> None:
        register_pages(self)
        self.logger.info("kanban plugin registered")
