"""Process entrypoint: reads Settings from the environment and runs NiceGUI.

Everything about *what pages exist* lives in :mod:`hermes_nicegui.web`. This
module's only job is assembling the real ``PluginContext`` (real ``Settings``
from the environment, a real ``HermesClient``) and handing it to
``web.build``. Plugins should never import from here -- ``hermes_nicegui.web``
is the public surface they and the test suite depend on instead.
"""

from __future__ import annotations

from loguru import logger
from nicegui import app, ui

from hermes_nicegui import web
from hermes_nicegui.config import Settings
from hermes_nicegui.gateway import HermesClient
from hermes_nicegui.logging import setup_logging
from hermes_nicegui.plugin import PluginContext


def _bootstrap(settings: Settings) -> None:
    """Build the real plugin context and register pages.

    Registered as a NiceGUI startup handler so that plugin ``@ui.page``
    decorators run inside the ``on_startup`` phase (required in script mode).
    """
    setup_logging(settings.log_level)
    client = HermesClient(**settings.client_kwargs())
    context = PluginContext(client=client, settings=settings, logger=logger)
    web.build(context)


def main() -> None:
    """Console entry point (``hermes-nicegui``)."""
    settings = Settings()
    app.on_startup(lambda: _bootstrap(settings))
    ui.run(
        title="Hermes",
        host=settings.ui_host,
        port=settings.ui_port,
        storage_secret="hermes-nicegui",
        reload=settings.ui_reload,
        show=False,
    )
