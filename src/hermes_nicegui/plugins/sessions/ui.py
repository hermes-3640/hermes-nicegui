"""Sessions plugin pages: session list, a per-session event timeline, and chat.

The transcript reads as a compact chat: what was actually *said* (user
input, Hermes' replies) renders as visible bubbles; everything mechanical
that happened in between (reasoning, tool calls, tool results) renders as a
single collapsed row per event, identified by icon and color rather than a
repeated text label -- see ``_step``/``_tool_round_trip``/``_chat_bubble``.
Timestamps are available on hover (tooltip) rather than printed on every row.

Session data is read straight off the daemon's own SQLite `state.db` for
whichever profile is active for this browser tab
(``hermes_nicegui.web.current_store``) -- not the gateway's single-profile
bearer-token API, and not the `hermes` CLI either (reads only shell out to
the CLI for rename/delete, which mutate state the daemon itself owns). There's
no create/fork-session mutation available at all, so unlike the old
gateway-backed version: there's no "New session" that pre-creates an empty
row, and no fork button. Sending a message happens on the chat page
(``/sessions/chat``), which runs an interactive ``hermes chat`` in a pty --
see ``plugins/terminal/ui.py`` for the pattern this reuses. Session detail is
a read-only transcript + rename/delete/continue-in-chat.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any

from nicegui import app, background_tasks, ui
from nicegui.events import XtermDataEventArguments, XtermResizeEventArguments

from hermes_nicegui import web
from hermes_nicegui.gateway import HermesError, Message, Session
from hermes_nicegui.pagination import Pager, render_pager
from hermes_nicegui.plugin import Plugin
from hermes_nicegui.plugins.sessions.logic import (
    fmt_age,
    fmt_cost,
    fmt_ts,
    is_unread,
    oneline,
    pretty_yaml,
    source_icon,
)
from hermes_nicegui.plugins.terminal.logic import PtySession
from hermes_nicegui.web import current_profile, frame

SOURCES = {"": "All sources", "webui": "Web UI", "api_server": "API", "cron": "Cron"}
_SEEN_KEY = "sessions_seen"


def _unread_state() -> dict[str, float]:
    """Per-browser map of session_id -> last-seen timestamp."""
    try:
        return app.storage.user.setdefault(_SEEN_KEY, {})
    except RuntimeError:  # pragma: no cover - storage disabled in some test setups
        return {}


# -- transcript rendering ---------------------------------------------------
#
# Every event -- said or done -- gets exactly one ``ui.timeline_entry``. The
# icon+color say what kind of event it is; no separate title/subtitle text
# duplicates that. Timestamps sit behind a hover tooltip on that same icon
# (see ``_icon_tooltip``) rather than a separate element or a permanently
# visible subtitle.

# Quasar's own rule for an icon entry's subtitle is `padding-top: 8px`
# (`.q-timeline__entry--icon .q-timeline__subtitle`); the icon glyph itself
# is absolutely positioned at `top: 0` of the entry, so 8px still sits a
# few pixels below its vertical center. Halved to bring the two flush.
# Quasar also styles the subtitle (and, in dense-left mode, the content/
# title column) as small-caps and right-justified, both meant for a short
# date label next to the icon -- wrong for the sentence- and JSON-length
# text actually going there here.
_TIMELINE_CSS = """
.q-timeline__entry--icon .q-timeline__subtitle {
    padding-top: 4px !important;
}
.q-timeline__subtitle,
.q-timeline__content,
.q-timeline__title {
    text-align: left !important;
    text-transform: none !important;
    letter-spacing: normal !important;
}
.q-timeline__content {
    padding: 0 !important;
}
"""


def _icon_tooltip(entry: ui.timeline_entry, timestamp: float | None) -> None:
    """A native hover tooltip on the entry's own dot -- not a separate icon.

    ``ui.timeline_entry`` has no slot for its dot (confirmed against
    Quasar's ``QTimelineEntry`` source: the icon comes only from a prop,
    rendered with no slot to hook into), so this reaches into the rendered
    DOM directly and sets a plain ``title`` attribute on ``.q-timeline__dot``.
    A native tooltip has zero layout footprint -- nothing to misalign or
    resize on hover, unlike a Quasar ``q-tooltip`` sized against a
    full-row anchor.
    """
    text = json.dumps(fmt_ts(timestamp))
    ui.run_javascript(
        f'getHtmlElement({entry.id}).querySelector(".q-timeline__dot").title = {text}'
    )


def _collapsible_entry(
    icon: str,
    color: str,
    summary: str,
    timestamp: float | None,
    render_body: Callable[[], None] | None,
) -> None:
    """A timeline entry whose header *is* the summary, via Quasar's
    ``subtitle`` slot.

    Quasar's own CSS gives ``.q-timeline__entry--icon .q-timeline__subtitle``
    just ``padding-top: 8px``, versus the much larger offset on
    ``.q-timeline__content``/``title`` -- so the subtitle slot sits close to
    where the icon itself is drawn (``.q-timeline__dot .q-icon`` is
    absolutely positioned at ``top: 0`` of the entry). The default ``title``
    slot could not be made to line up with the icon without fighting that
    layout; the subtitle slot already does, natively.
    """
    entry = ui.timeline_entry(icon=icon, color=color)
    _icon_tooltip(entry, timestamp)
    with entry.add_slot("subtitle"):
        if render_body is None:
            ui.label(summary).classes("text-xs w-full")
        else:
            with ui.expansion(summary).props("dense").classes("text-xs w-full"):
                render_body()


def _chat_bubble(icon: str, color: str, sender: str, content: str, timestamp: float | None) -> None:
    """A visible chat message -- something the user or Hermes actually said."""
    entry = ui.timeline_entry(icon=icon, color=color)
    _icon_tooltip(entry, timestamp)
    with entry.add_slot("subtitle"):
        ui.label(sender).classes("text-xs font-bold w-full")
    with entry, ui.card().props("flat bordered").classes("w-fit max-w-full q-mt-xs q-mb-md"):
        ui.markdown(content)


def _step(icon: str, color: str, text: str, timestamp: float | None, *, mono: bool = False) -> None:
    """One compact row for a mechanical event (not something said).

    Text that already fits on one line *is* the header, nothing to expand.
    Longer text collapses to its first line as the header, with the rest
    revealed on click: prose (reasoning) as markdown, structured data (a
    tool result) as YAML in a code viewer -- see :func:`pretty_yaml`.
    """
    summary = oneline(text)
    if summary == text.strip():
        _collapsible_entry(icon, color, text, timestamp, None)
        return

    def _body() -> None:
        if mono:
            ui.code(pretty_yaml(text), language="yaml").classes("w-full")
        else:
            ui.markdown(text)

    _collapsible_entry(icon, color, summary, timestamp, _body)


def _tool_round_trip(
    name: str, args: str | None, result: Message | None, timestamp: float | None
) -> None:
    """One entry for a tool call *and* its own result, not two.

    A "Called X" row followed immediately by an "X result" row says the tool
    name twice for what a reader experiences as a single step. The tool name
    plus a short preview of its arguments is the row's always-visible label;
    the (often much longer) result -- both usually JSON on the wire -- sits
    behind the same toggle, re-rendered as YAML (see :func:`pretty_yaml`)
    so multi-line values inside it read as actual lines, not `\n` escapes.
    """
    label = f"{name}: {oneline(args, limit=60)}" if args else name

    def _body() -> None:
        if args:
            ui.code(pretty_yaml(args), language="yaml").classes("w-full")
        if result and result.content:
            if args:
                ui.separator()
            ui.code(pretty_yaml(result.content), language="yaml").classes("w-full")

    has_body = bool(args) or bool(result and result.content)
    _collapsible_entry("construction", "grey", label, timestamp, _body if has_body else None)


def render_transcript_events(messages: list[Message]) -> None:
    """Render the whole transcript as a true, chronological event timeline.

    "You" and "Hermes" -- the things actually said -- stay fully visible;
    everything else (reasoning, tool calls, tool results) collapses by
    default so the conversation itself isn't buried in mechanics. A tool
    call and its own result (matched by ``tool_call_id``, not just assumed
    to be the next message) render as one fused entry -- see
    :func:`_tool_round_trip`.
    """
    results_by_call_id = {
        m.tool_call_id: m for m in messages if m.role == "tool" and m.tool_call_id
    }
    consumed_result_ids = {m.id for m in results_by_call_id.values()}

    for msg in messages:
        if msg.role == "user":
            _chat_bubble("person", "primary", "You", msg.content or "", msg.timestamp)

        elif msg.role == "assistant":
            if msg.reasoning:
                _step("psychology", "amber", msg.reasoning, msg.timestamp)
            for call in msg.tool_calls or []:
                fn = call.get("function", {})
                call_id = call.get("id")
                result = results_by_call_id.get(call_id) if call_id else None
                _tool_round_trip(fn.get("name", "tool"), fn.get("arguments"), result, msg.timestamp)
            if msg.content:
                _chat_bubble("smart_toy", "secondary", "Hermes", msg.content, msg.timestamp)

        elif msg.id not in consumed_result_ids:
            # An orphaned tool result: its call fell outside this page of
            # messages, so there was nothing to fuse it with above.
            _step("output", "teal", msg.content or "(empty)", msg.timestamp, mono=True)


# -- chat (pty) ---------------------------------------------------------
#
# Hermes's own web dashboard doesn't call a chat-send API at all -- it runs
# `hermes chat` attached to a pty. This app runs on the same host as the
# real Hermes install (or reaches it over ssh -- see `HermesExecutor`), so
# it does the same thing directly: reuses `PtySession` from the terminal
# plugin unchanged, just with a command instead of a bare shell.


def _register_chat_page(plugin: Plugin) -> None:
    logger = plugin.logger

    async def _chat_page(resume: str | None) -> None:
        with frame(active="/sessions"):
            profile = current_profile()
            ui.label(f"Chat — {profile or 'default'}").classes("text-lg")

            terminal = (
                ui.xterm({"cursorBlink": True, "fontSize": 14})
                .classes("w-full h-[70vh]")
                .mark("chat-terminal")
            )

            args = ["chat"]
            if resume:
                args += ["--resume", resume]
            executor = web.current_executor()
            assert executor is not None
            full_argv = executor.argv(*args, profile=profile, tty=True)
            session = PtySession(shell=full_argv[0], args=full_argv[1:])
            fd = session.start()
            loop = asyncio.get_event_loop()

            def _pump() -> None:
                data = session.read()
                if data:
                    terminal.write(data)
                elif data is None:
                    loop.remove_reader(fd)
                    terminal.writeln("\r\n[hermes chat exited]")

            loop.add_reader(fd, _pump)

            def _on_data(e: XtermDataEventArguments) -> None:
                try:
                    session.write(e.data.encode())
                except OSError:
                    pass

            def _on_resize(e: XtermResizeEventArguments) -> None:
                try:
                    session.resize(e.cols, e.rows)
                except OSError:
                    pass

            terminal.on_data(_on_data)
            terminal.on_resize(_on_resize)

            def _cleanup() -> None:
                loop.remove_reader(fd)
                session.close()
                logger.debug("chat pty {} closed", session.pid)

            ui.context.client.on_disconnect(_cleanup)

            # See `plugins/terminal/ui.py` for why this is a ResizeObserver
            # rather than a one-off `fit()`.
            ui.run_javascript(f"""
                (() => {{
                    const el = getHtmlElement({terminal.id});
                    if (!el) return;
                    new ResizeObserver(() => getElement({terminal.id}).fit()).observe(el);
                }})();
            """)

            logger.debug("chat page rendered (profile={}, resume={})", profile, resume)

    @ui.page("/sessions/chat")
    async def new_chat_page() -> None:
        await _chat_page(None)

    @ui.page("/sessions/chat/{session_id}")
    async def resume_chat_page(session_id: str) -> None:
        await _chat_page(session_id)


# -- pages --------------------------------------------------------------


def register_pages(plugin: Plugin) -> None:
    logger = plugin.logger
    _register_chat_page(plugin)

    def _mark_read(session_id: str) -> None:
        _unread_state()[session_id] = datetime.now().timestamp()

    def _run_in_client(coro_factory: Callable[[], Any]) -> None:
        """Schedule an async handler as a background task, but re-enter the
        calling client's slot context inside that task first.

        ``ui.notify``/``ui.navigate.to`` resolve the current *client* from
        the calling asyncio task's own slot stack (see ``nicegui.context``).
        A plain ``background_tasks.create(coro)`` spawns a genuinely new task
        with an empty stack, so both silently no-op there (the resulting
        ``RuntimeError`` is swallowed by the task's own exception handler) --
        this is why "click Fork/Delete/New session" appeared to do the
        network call but never notified or navigated. Capturing the client
        here, while still in the click handler's own task (which does have a
        stack), and re-entering it with ``with page_client:`` inside the
        task mirrors what NiceGUI itself does for a plain ``async def``
        event handler (see ``events.handle_event``'s
        ``_await_and_handle_in_context``).
        """
        page_client = ui.context.client
        coro = coro_factory()

        async def run() -> None:
            with page_client:
                await coro

        background_tasks.create(run())

    def _rename(session_id: str, new_title: str, on_renamed: Callable[[], Any] | None) -> None:
        async def do_rename() -> None:
            if not new_title.strip():
                return
            try:
                await web.current_store().sessions.rename(session_id, new_title.strip())
            except HermesError as exc:
                ui.notify(f"Rename failed: {exc}", type="negative")
                return
            ui.notify("Session renamed", type="positive")
            if on_renamed:
                on_renamed()

        _run_in_client(do_rename)

    def _delete(session_id: str, on_deleted: Callable[[], Any] | None) -> None:
        async def do_delete() -> None:
            try:
                await web.current_store().sessions.delete(session_id)
            except HermesError as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")
                return
            ui.notify("Session deleted", type="positive")
            _unread_state().pop(session_id, None)
            if on_deleted:
                on_deleted()

        _run_in_client(do_delete)

    @ui.page("/sessions", title="Sessions")
    async def sessions_page() -> None:
        with frame(active="/sessions"):
            with ui.row().classes("w-full items-center gap-2"):
                search = (
                    ui.input(placeholder="Search")
                    .props("outlined dense clearable")
                    .classes("flex-grow")
                    .on("change", lambda: _search_or_source_changed())
                )
                source_filter = (
                    ui.select(SOURCES, value="")
                    .props("outlined dense options-dense")
                    .on("update:model-value", lambda: _search_or_source_changed())
                )
                unread_only = ui.switch("Unread").on(
                    "update:model-value", lambda: render_rows()
                )
                ui.button(
                    icon="refresh", on_click=lambda: background_tasks.create(_refresh())
                ).props("flat round dense").mark("refresh-button").tooltip("Refresh")
                ui.button(
                    "New chat",
                    icon="add",
                    on_click=partial(ui.navigate.to, "/sessions/chat"),
                ).props("unelevated").mark("new-session-button")

            list_container = ui.list().props("separator").classes("w-full")

            current_sessions: list[Session] = []
            total = 0
            pager = Pager()

            def render_preview_row(s: Session, *, unread: bool) -> None:
                with (
                    ui.item(on_click=partial(ui.navigate.to, f"/sessions/{s.id}"))
                    .props("v-ripple")
                    .mark("session-row")
                ):
                    with ui.item_section().props("avatar"):
                        ui.icon(source_icon(s.source))
                    with ui.item_section():
                        ui.item_label(s.title or "(untitled)").classes(
                            "font-bold" if unread else ""
                        )
                        ui.item_label(s.preview or "No preview").props("caption lines=1")
                    with ui.item_section().props("side top"):
                        ui.label(fmt_age(s.last_active)).classes("text-xs opacity-60")
                        if unread:
                            ui.icon("circle", size="8px", color="primary")

            def render_rows() -> None:
                """Render the current page's already-fetched sessions.

                "Unread" is per-browser state (``app.storage.user``), not
                something the daemon's own `state.db` knows about, so unlike
                search/source it can't be pushed into the SQL query -- it
                narrows the current page's rows client-side instead, without
                re-fetching or changing what the pager's own page count is
                based on.
                """
                state = _unread_state()
                rows = current_sessions
                if unread_only.value:
                    rows = [s for s in rows if is_unread(s, state)]
                list_container.clear()
                with list_container:
                    if not rows:
                        ui.label("No sessions.").classes("text-sm opacity-60 q-pa-sm")
                    for s in rows:
                        render_preview_row(s, unread=is_unread(s, state))
                    render_pager(pager, total, lambda: background_tasks.create(fetch()))

            async def fetch() -> None:
                nonlocal total
                list_container.clear()
                with list_container, ui.item():
                    ui.spinner(size="lg")
                try:
                    current_sessions[:], total = await web.current_store().sessions.list(
                        source=source_filter.value or None,
                        search=(search.value or "").strip() or None,
                        limit=pager.limit,
                        offset=pager.offset,
                    )
                except HermesError as exc:
                    list_container.clear()
                    ui.notify(f"Failed to load sessions: {exc}", type="negative")
                    return
                render_rows()

            def _search_or_source_changed() -> None:
                pager.reset()
                background_tasks.create(fetch())

            async def _refresh() -> None:
                pager.reset()
                await fetch()

            # NiceGUI aborts the whole page-build coroutine with a 500 if it
            # doesn't finish within `response_timeout` (default 3s -- the
            # exact number this page used to blow past). `client.connected()`
            # tells NiceGUI to deliver the shell built so far *now* and keep
            # running this coroutine in the background against the socket
            # that connects moments later, instead of racing the real
            # `hermes` subprocess call against that deadline.
            await ui.context.client.connected()
            await fetch()
            logger.debug("sessions list rendered")

    @ui.page("/sessions/{session_id}", title="Session")
    async def session_detail_page(session_id: str) -> None:
        ui.add_css(_TIMELINE_CSS)
        with frame(active="/sessions"):
            await ui.context.client.connected()
            try:
                session, messages = await web.current_store().sessions.get_detail(session_id)
            except HermesError as exc:
                ui.label(f"Failed to load session: {exc}")
                return
            _mark_read(session_id)

            with ui.card():
                with ui.row():
                    ui.label(session.title or session.id)
                    ui.space()
                    ui.badge(session.source or "unknown", color="grey")
                    ui.badge(session.model or "no model", color="primary")
                ui.label(
                    f"ID {session.id} · {session.message_count} messages · "
                    f"{session.tool_call_count} tool calls · "
                    f"cost {fmt_cost(session.estimated_cost_usd)}"
                )
                with ui.row():
                    ui.input(
                        "Rename",
                        placeholder=session.title or "",
                        on_change=lambda e: _rename(session_id, e.value or "", None),
                    ).props("outlined")
                    ui.button(
                        "Continue in chat",
                        icon="chat",
                        on_click=partial(ui.navigate.to, f"/sessions/chat/{session_id}"),
                    )
                    ui.button(
                        "Delete",
                        icon="delete",
                        color="negative",
                        on_click=partial(_delete, session_id, lambda: ui.navigate.to("/sessions")),
                    )

            if not messages:
                ui.label("No messages yet.")
            else:
                with ui.timeline(layout="dense").classes("w-full"):
                    render_transcript_events(messages)

            logger.debug("session detail rendered for {}", session_id)
