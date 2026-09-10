"""Verify sidebar renders anchor elements (a[href]), not buttons."""
import pytest
from nicegui import ui
from nicegui.testing import User


@pytest.mark.asyncio
async def test_sidebar_has_anchor_elements(user: User, make_context):
    """Sidebar nav items should render as <a href> elements, not buttons."""
    from hermes_nicegui.web import build
    from hermes_nicegui.plugin import NavItem, Plugin

    class DummyPlugin(Plugin):
        name = "test"
        route = "/sessions"
        title = "Sessions"
        icon = "chat"

        def nav_items(self):
            return [
                NavItem("/sessions", "Sessions", "chat"),
                NavItem("/cron", "Cron Jobs", "schedule"),
            ]

        def register(self):
            pass

    ctx = make_context()
    build(ctx, [DummyPlugin(ctx)])

    await user.open("/")

    # ui.element('a') renders as raw <a> tags; verify they exist in the DOM
    all_a = [
        e for e in user.find(kind=ui.element).elements if getattr(e, 'tag', None) == 'a'
    ]

    assert len(all_a) >= 2, f"Expected ≥2 <a> elements, got {len(all_a)}"

    # Verify they have proper href targets matching the nav item routes
    targets = {getattr(e, '_props', {}).get('href', None) for e in all_a}
    assert "/sessions" in targets, f"Sessions link href missing. Got: {targets}"
    assert "/cron" in targets, f"Cron link href missing. Got: {targets}"
