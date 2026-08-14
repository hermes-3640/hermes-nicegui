"""Sessions plugin pages: session list + a per-session event timeline.

The transcript reads as a compact chat: what was actually *said* (user
input, Hermes' replies) renders as visible bubbles; everything mechanical
that happened in between (reasoning, tool calls, tool results) renders as a
single collapsed row per event, identified by icon and color rather than a
repeated text label -- see ``_step``/``_tool_round_trip``/``_chat_bubble``.
Timestamps are available on hover (tooltip) rather than printed on every row.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any

from nicegui import app, background_tasks, ui

from hermes_nicegui.gateway import HermesError, Message, Session
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
from hermes_nicegui.web import frame

PAGE_SIZE = 50
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


def _scroll_to_bottom(anchor: ui.element) -> None:
    """Keep the newest content in view as a live turn grows the page.

    Targets both plain ``window`` scroll and Quasar's own
    ``.q-page-container`` scroll region, since which one actually owns the
    scrollbar depends on layout/viewport and there's no cheap way to ask.

    ``anchor`` must be an element belonging to the page's own client --
    ``ui.run_javascript`` resolves *which* browser client to send the script
    to from the current slot stack, which a background task (this always
    runs from one -- see ``background_tasks.create(send())``) starts out
    with none of, unlike a normal page-request task.
    """
    with anchor:
        ui.run_javascript(
            "window.scrollTo(0, document.body.scrollHeight);"
            "document.querySelectorAll('.q-page-container').forEach("
            "el => el.scrollTop = el.scrollHeight);"
        )


def _chat_bubble_shell(icon: str, color: str, sender: str, timestamp: float | None) -> ui.markdown:
    """Build a chat bubble's entry+card shell and return its markdown body.

    Split out from :func:`_chat_bubble` so a live-streamed reply can build
    the same shell once and keep updating the markdown's content as deltas
    arrive, instead of re-rendering a whole new bubble per token.
    """
    entry = ui.timeline_entry(icon=icon, color=color)
    _icon_tooltip(entry, timestamp)
    with entry.add_slot("subtitle"):
        ui.label(sender).classes("text-xs font-bold w-full")
    with entry, ui.card().props("flat bordered").classes("w-fit max-w-full q-mt-xs q-mb-md"):
        return ui.markdown()


def _chat_bubble(icon: str, color: str, sender: str, content: str, timestamp: float | None) -> None:
    """A visible chat message -- something the user or Hermes actually said."""
    _chat_bubble_shell(icon, color, sender, timestamp).set_content(content)


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


# -- pages --------------------------------------------------------------


def register_pages(plugin: Plugin) -> None:
    client = plugin.client
    logger = plugin.logger

    def _mark_read(session_id: str) -> None:
        _unread_state()[session_id] = datetime.now().timestamp()

    async def render_transcript(container: ui.element, session_id: str) -> ui.timeline | None:
        """Render a session's messages into ``container`` as an event timeline.

        Returns the ``ui.timeline`` element (or ``None`` if there was nothing
        to show) so a live chat turn can append its own entries to the same
        timeline instead of only being able to replace the whole container.
        """
        container.clear()
        with container:
            try:
                messages = await client.session_messages(session_id, limit=200)
            except HermesError as exc:
                ui.label(f"Failed to load messages: {exc}")
                return None
            if not messages:
                ui.label("No messages yet.")
                return None
            with ui.timeline(layout="dense").classes("w-full") as timeline:
                render_transcript_events(messages)
            return timeline

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
                await client.update_session(session_id, title=new_title.strip())
            except HermesError as exc:
                ui.notify(f"Rename failed: {exc}", type="negative")
                return
            ui.notify("Session renamed", type="positive")
            if on_renamed:
                on_renamed()

        _run_in_client(do_rename)

    def _fork(session_id: str) -> None:
        async def do_fork() -> None:
            try:
                new = await client.fork_session(session_id)
            except HermesError as exc:
                ui.notify(f"Fork failed: {exc}", type="negative")
                return
            ui.notify("Forked session", type="positive")
            ui.navigate.to(f"/sessions/{new.id}")

        _run_in_client(do_fork)

    def _new_session() -> None:
        async def do_create() -> None:
            try:
                session = await client.create_session()
            except HermesError as exc:
                ui.notify(f"Failed to start session: {exc}", type="negative")
                return
            ui.navigate.to(f"/sessions/{session.id}")

        _run_in_client(do_create)

    def _delete(session_id: str, on_deleted: Callable[[], Any] | None) -> None:
        async def do_delete() -> None:
            try:
                await client.delete_session(session_id)
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
                    .on("change", lambda: background_tasks.create(load_list()))
                )
                source_filter = ui.select(SOURCES, value="").props("outlined dense options-dense")
                unread_only = ui.switch("Unread")
                ui.button(
                    icon="refresh", on_click=lambda: background_tasks.create(load_list())
                ).props("flat round dense").mark("refresh-button").tooltip("Refresh")
                ui.button("New session", icon="add", on_click=_new_session).props(
                    "unelevated"
                ).mark("new-session-button")

            list_container = ui.list().props("separator").classes("w-full")

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

            async def load_list() -> None:
                try:
                    sessions, _ = await client.list_sessions(
                        limit=PAGE_SIZE, source=source_filter.value or None
                    )
                except HermesError as exc:
                    ui.notify(f"Failed to load sessions: {exc}", type="negative")
                    return
                state = _unread_state()
                query = (search.value or "").lower()
                list_container.clear()
                with list_container:
                    for s in sessions:
                        if unread_only.value and not is_unread(s, state):
                            continue
                        if query:
                            haystack = f"{s.title or ''} {s.preview or ''} {s.source or ''}".lower()
                            if query not in haystack:
                                continue
                        render_preview_row(s, unread=is_unread(s, state))

            await load_list()
            logger.debug("sessions list rendered")

    @ui.page("/sessions/{session_id}", title="Session")
    async def session_detail_page(session_id: str) -> None:
        ui.add_css(_TIMELINE_CSS)
        with frame(active="/sessions"):
            try:
                session = await client.get_session(session_id)
            except HermesError as exc:
                ui.label(f"Failed to load session: {exc}")
                return
            _mark_read(session_id)

            def _stats_text() -> str:
                return (
                    f"ID {session.id} · {session.message_count} messages · "
                    f"{session.tool_call_count} tool calls · "
                    f"cost {fmt_cost(session.estimated_cost_usd)}"
                )

            with ui.card():
                with ui.row():
                    ui.label(session.title or session.id)
                    ui.space()
                    ui.badge(session.source or "unknown", color="grey")
                    ui.badge(session.model or "no model", color="primary")
                stats_label = ui.label(_stats_text())
                with ui.row():
                    ui.input(
                        "Rename",
                        placeholder=session.title or "",
                        on_change=lambda e: _rename(session_id, e.value or "", None),
                    ).props("outlined")
                    ui.button("Fork", icon="call_split", on_click=partial(_fork, session_id))
                    ui.button(
                        "Delete",
                        icon="delete",
                        color="negative",
                        on_click=partial(_delete, session_id, lambda: ui.navigate.to("/sessions")),
                    )

            transcript = ui.column().classes("w-full")
            timeline = await render_transcript(transcript, session_id)
            _scroll_to_bottom(transcript)

            def _timeline() -> ui.timeline:
                """The transcript's live timeline, creating an empty one the
                first time a chat turn is sent to a session with no history
                yet (``render_transcript`` returns ``None`` there since it
                has nothing of its own to show)."""
                nonlocal timeline
                if timeline is None:
                    transcript.clear()
                    with transcript:
                        timeline = ui.timeline(layout="dense").classes("w-full")
                return timeline

            async def send() -> None:
                nonlocal session, timeline
                text = (message_input.value or "").strip()
                if not text:
                    return
                message_input.set_value("")
                message_input.disable()
                send_button.disable()
                try:
                    with _timeline():
                        _chat_bubble("person", "primary", "You", text, datetime.now().timestamp())
                        with ui.row().classes(
                            "items-center gap-2 text-xs opacity-60 q-mb-sm"
                        ) as status_row:
                            ui.spinner(size="1em")
                            status_label = ui.label("Thinking…")
                        reply_markdown = _chat_bubble_shell(
                            "smart_toy", "secondary", "Hermes", None
                        )
                    _scroll_to_bottom(transcript)
                    content = ""
                    try:
                        async for event in client.stream_turn(
                            session_id, text, model=session.model
                        ):
                            if event.event == "tool.progress":
                                name = event.data.get("tool_name", "tool")
                                delta = oneline(event.data.get("delta", ""), limit=60)
                                status_label.set_text(f"{name}: {delta}" if delta else name)
                            elif event.event == "assistant.delta":
                                status_row.set_visibility(False)
                                content += event.data.get("delta", "")
                                reply_markdown.set_content(content)
                            elif event.event == "assistant.completed":
                                status_row.set_visibility(False)
                                content = event.data.get("content", content)
                                reply_markdown.set_content(content)
                            _scroll_to_bottom(transcript)
                    except HermesError as exc:
                        status_row.set_visibility(False)
                        ui.notify(f"Message failed: {exc}", type="negative")
                finally:
                    message_input.enable()
                    send_button.enable()
                    message_input.run_method("focus")

                timeline = await render_transcript(transcript, session_id)
                _scroll_to_bottom(transcript)
                try:
                    session = await client.get_session(session_id)
                except HermesError:
                    pass
                else:
                    stats_label.set_text(_stats_text())

            with ui.card().classes("w-full sticky bottom-0 z-10"):
                with ui.row().classes("w-full items-center gap-2"):
                    message_input = (
                        ui.input(placeholder="Message Hermes…")
                        .props("outlined dense autofocus")
                        .classes("flex-grow")
                        .on("keydown.enter", lambda: _run_in_client(send))
                        .mark("chat-input")
                    )
                    send_button = (
                        ui.button(icon="send", on_click=lambda: _run_in_client(send))
                        .props("round dense")
                        .mark("chat-send")
                    )

            logger.debug("session detail rendered for {}", session_id)
