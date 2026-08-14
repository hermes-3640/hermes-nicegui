"""Cron plugin: browse and manage scheduled Hermes jobs."""

from __future__ import annotations

from hermes_nicegui.plugin import Plugin

from .ui import register_pages


class CronPlugin(Plugin):
    name = "cron"
    title = "Cron Jobs"
    icon = "event_repeat"
    route = "/cron"

    def register(self) -> None:
        register_pages(self)
        self.logger.info("cron plugin registered")
