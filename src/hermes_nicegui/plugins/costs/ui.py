"""NiceGUI page for provider costs."""

from __future__ import annotations

import asyncio

from nicegui import ui

from hermes_nicegui.plugin import Plugin
from hermes_nicegui.web import frame

from .logic import CostEntry, CostProvider, CostSummary


def _money(value: float | None, currency: str = "USD") -> str:
    """Format a cost value for display."""
    if value is None:
        return "Unavailable"
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{value:,.2f}"


def _when(value) -> str:
    return value.isoformat(timespec="seconds") if value is not None else ""


def register_pages(plugin: Plugin) -> None:
    """Register the costs page."""
    providers: list[CostProvider] = plugin.providers  # type: ignore[attr-defined]

    @ui.page("/costs", title="Costs")
    async def costs_page() -> None:
        with frame(active="/costs"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Costs").classes("text-lg")
                ui.space()
                refresh = ui.button("Refresh")
            content = ui.column().classes("w-full gap-4")

            async def load() -> None:
                content.clear()
                summaries = await asyncio.gather(
                    *(provider.summary() for provider in providers), return_exceptions=True
                )
                usages = await asyncio.gather(
                    *(
                        provider.usage() if provider.available else _empty_usage()
                        for provider in providers
                    ),
                    return_exceptions=True,
                )
                with content:
                    with ui.row().classes("w-full items-stretch gap-4 flex-wrap"):
                        for provider, result in zip(providers, summaries, strict=True):
                            _render_summary(provider, result)
                    for provider, result in zip(providers, usages, strict=True):
                        if isinstance(result, list) and result and any(
                            entry.when is not None for entry in result
                        ):
                            _render_usage(provider, result)

            async def refresh_page() -> None:
                await load()

            refresh.on_click(refresh_page)
            await load()


async def _empty_usage() -> list[CostEntry]:
    return []


def _render_summary(provider: CostProvider, result: object) -> None:
    with ui.card().classes("min-w-64 flex-1"):
        ui.label(provider.label).classes("text-lg font-medium")
        if not provider.available:
            ui.label("not configured (missing key / no data file)").classes("text-sm opacity-60")
        elif isinstance(result, Exception):
            ui.badge(str(result), color="amber")
        else:
            summary: CostSummary = result  # type: ignore[assignment]
            if summary.total is not None:
                ui.label(
                    f"{_money(summary.remaining, summary.currency)} remaining this month"
                ).classes("text-2xl")
                ui.label(f"of {_money(summary.total, summary.currency)}")
                ui.linear_progress(
                    max(0.0, min(1.0, (summary.remaining or 0) / summary.total)), show_value=False
                )
            elif summary.used is not None:
                ui.label(f"{_money(summary.used, summary.currency)} this month").classes("text-2xl")
            for window in summary.windows:
                if window.limit is not None:
                    text = (
                        f"{window.label}: {_money(window.remaining, summary.currency)} remaining "
                        f"of {_money(window.limit, summary.currency)}"
                    )
                else:
                    text = f"{window.label}: {_money(window.used, summary.currency)} used"
                if window.resets_at is not None:
                    text += f" · resets {window.resets_at.isoformat(timespec='minutes')}"
                ui.label(text)
            if summary.as_of:
                ui.label(f"As of {_when(summary.as_of)}").classes("text-xs opacity-60")
            if summary.note:
                ui.label(summary.note).classes("text-sm opacity-60")


def _render_usage(provider: CostProvider, entries: list[CostEntry]) -> None:
    ui.label(f"{provider.label} usage").classes("text-lg")
    columns = [
        {"name": "when", "label": "When", "field": "when"},
        {"name": "provider", "label": "Provider", "field": "provider"},
        {"name": "model", "label": "Model", "field": "model"},
        {"name": "label", "label": "Label", "field": "label"},
        {"name": "cost", "label": "Cost", "field": "cost"},
        {"name": "tokens", "label": "Tokens in/out", "field": "tokens"},
    ]
    rows = [
        {
            "when": _when(entry.when),
            "provider": entry.provider or provider.label,
            "model": entry.model or "",
            "label": entry.label,
            "cost": _money(entry.cost),
            "tokens": f"{entry.tokens_input or ''}/{entry.tokens_output or ''}",
        }
        for entry in entries
    ]
    ui.table(columns=columns, rows=rows, row_key="label").classes("w-full")
