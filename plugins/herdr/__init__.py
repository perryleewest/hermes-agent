"""Herdr integration plugin — bundled, auto-loaded.

Gives Hermes control of `Herdr <https://github.com/herdrdev/herdr>`_, the
terminal runtime coding agents live in. Herdr already recognises Hermes from
its side (it ships a ``hermes`` detection manifest, an ``Agent::Hermes`` kind,
and ``herdr integration install hermes``); this plugin closes the loop so
Hermes can drive Herdr back.

The tools default to *every* configured Herdr server at once, which is what
turns several machines into one control surface. See ``README.md`` for the
config block and the reasoning behind fanning out over SSH rather than using
Herdr's own ``machine add``.

Bundled + ``kind: backend`` auto-loads on startup (the same pattern
``plugins/spotify`` uses for an always-available service integration) — no
``plugins.enabled`` entry required. Every tool is gated by
``check_herdr_available()``, so the tools always appear in ``hermes tools``
but only dispatch where Herdr is actually reachable.
"""

from __future__ import annotations

from plugins.herdr.tools import (
    HERDR_AGENTS_SCHEMA,
    HERDR_BOARD_SCHEMA,
    HERDR_CONTROL_SCHEMA,
    HERDR_PANES_SCHEMA,
    HERDR_SERVERS_SCHEMA,
    check_herdr_available,
    handle_herdr_agents,
    handle_herdr_board,
    handle_herdr_control,
    handle_herdr_panes,
    handle_herdr_servers,
)

_TOOLS = (
    ("herdr_servers", HERDR_SERVERS_SCHEMA, handle_herdr_servers, "🖥️"),
    ("herdr_agents",  HERDR_AGENTS_SCHEMA,  handle_herdr_agents,  "🤖"),
    ("herdr_control", HERDR_CONTROL_SCHEMA, handle_herdr_control, "🎛️"),
    ("herdr_panes",   HERDR_PANES_SCHEMA,   handle_herdr_panes,   "🪟"),
    ("herdr_board",   HERDR_BOARD_SCHEMA,   handle_herdr_board,   "👓"),
)


def register(ctx) -> None:
    """Register all Herdr tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="herdr",
            schema=schema,
            handler=handler,
            check_fn=check_herdr_available,
            emoji=emoji,
        )
