"""The web UI shell: shared frame, nav state, and page registration.

This is the boundary plugins are allowed to depend on. ``hermes_nicegui.app``
owns *process* bootstrap (reading ``Settings()`` from the environment,
building the real ``HermesClient``, calling ``ui.run``); this module owns
*page* bootstrap and knows nothing about where its ``PluginContext`` came
from. That split is what lets :func:`build` be called directly from a test
with a fake context and no environment variables involved, instead of going
through the whole process entrypoint.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING

from loguru import logger as _logger
from nicegui import ui

from hermes_nicegui.plugin import NavItem, Plugin, PluginContext, load_plugins

if TYPE_CHECKING:
    from collections.abc import Iterator

    from hermes_nicegui.config import Settings
    from hermes_nicegui.gateway import HermesClient


class AppState:
    """Holds the assembled plugin set and nav items for the running app.

    A single process-wide instance (``state`` below), on purpose: nav items
    and settings are shared across every user session, not per-user data, so
    there is nothing to gain by threading them through every page function.
    :func:`build` resets it in place, which is what makes calling it again
    from a second test safe.
    """

    def __init__(self) -> None:
        self.settings: Settings | None = None
        self.client: HermesClient | None = None
        self.plugins: list[Plugin] = []
        self.nav_items: list[NavItem] = []
        self.logger = _logger


state = AppState()


def build(context: PluginContext, plugins: list[Plugin] | None = None) -> AppState:
    """Register the home page and every plugin's pages against ``state``.

    ``plugins`` are already-constructed ``Plugin`` instances. Pass an
    explicit list to register exactly the plugins a test cares about; omit
    it (the production path, see ``hermes_nicegui.app``) to discover the
    installed set through the ``hermes_nicegui.plugins`` entry-point group.

    Safe to call more than once in the same process: state is reset before
    each build, which is what the test suite relies on -- NiceGUI's ``user``
    test fixture resets the route table between tests, and each test calls
    ``build`` fresh with the plugin set it wants.
    """
    state.settings = context.settings
    state.client = context.client
    state.logger = context.logger
    state.plugins = plugins if plugins is not None else load_plugins(context)
    state.nav_items = []

    for plugin in state.plugins:
        try:
            plugin.register()
        except Exception:
            state.logger.exception("plugin {} failed to register", plugin.name)
            continue
        for item in plugin.nav_items():
            if item.route not in {nav.route for nav in state.nav_items}:
                state.nav_items.append(item)

    _register_home()

    state.logger.info(
        "hermes-nicegui ready: {} plugins, {} nav items",
        len(state.plugins),
        len(state.nav_items),
    )
    return state


@contextmanager
def frame(title: str, *, active: str = "") -> Iterator[None]:
    """Shared page frame: header + left nav drawer + content column.

    Every plugin page wraps its content in ``with frame(...):`` so the shell
    stays consistent across modules. ``active`` highlights the current route.
    Dark mode follows ``state.settings.ui_dark``.
    """
    ui.colors(primary="#4F46E5", secondary="#0EA5E9")
    if state.settings and state.settings.ui_dark:
        ui.dark_mode().enable()
    else:
        ui.dark_mode().disable()
    with ui.header():
        with ui.row().classes("items-center"):
            ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat round dense")
            ui.icon("device_hub")
            ui.label("Hermes")
            ui.space()
            ui.label(title)
            ui.space()

    # `value` deliberately left unset: NiceGUI opens the drawer above the
    # 1024px breakpoint and collapses it to a toggleable overlay below it.
    # Forcing `value=True` (the old code) defeated that and squeezed every
    # page's content into a sliver next to a permanently-open drawer on
    # phone-width screens.
    drawer = ui.left_drawer(fixed=False)
    with drawer, ui.list().props("dense"):
        for item in state.nav_items:
            with ui.item(on_click=partial(ui.navigate.to, item.route)).props(
                "v-ripple" + (" active active-class=text-primary" if item.route == active else "")
            ):
                with ui.item_section().props("avatar"):
                    if item.icon:
                        ui.icon(item.icon)
                with ui.item_section():
                    ui.item_label(item.label)

    with ui.column().classes("w-full"):
        yield


def _register_home() -> None:
    """Register the root landing page with quick links to plugins."""

    @ui.page("/", title="Hermes")
    def home_page() -> None:
        with frame("Home"):
            ui.label("Hermes NiceGUI")
            ui.label("Modular web UI for the Hermes Agent gateway.")
            with ui.row():
                for item in state.nav_items:
                    with ui.link(target=item.route).classes("no-underline"), ui.card():
                        with ui.row().classes("items-center gap-2"):
                            if item.icon:
                                ui.icon(item.icon)
                            ui.label(item.label)
