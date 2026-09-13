"""Hermes tools for driving Herdr across every machine at once.

The tools deliberately default to *all* configured servers. Herdr scopes
workspace, tab, pane ids and agent names to a single server -- two machines can
both have a ``w1:p1`` and both have an agent named ``reviewer`` -- so every
record returned here is tagged with the server it came from, and every mutating
call takes an explicit server.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from plugins.herdr import client as herdr_client
from plugins.herdr.client import HerdrError, HerdrResult, HerdrServer
from tools.registry import tool_error, tool_result

# Herdr's lifecycle vocabulary, ordered by how much they want a human.
# "blocked" is waiting on a decision; "done" finished and has not been looked
# at yet; the rest are informational.
STATE_PRIORITY = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}
VALID_STATES = ("idle", "working", "blocked", "done", "unknown")


def check_herdr_available() -> bool:
    """Runtime gate — see ``client.is_available``."""
    try:
        return herdr_client.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve(selector: Optional[str]) -> List[HerdrServer]:
    return herdr_client.resolve_servers(selector)


def _require_single_server(selector: Optional[str], *, action: str) -> HerdrServer:
    """Mutating calls must name exactly one server.

    Pane ids and agent names are not unique across servers, so "send this key
    to reviewer" is ambiguous the moment two machines are configured.
    """
    servers = _resolve(selector)
    if len(servers) == 1:
        return servers[0]
    names = ", ".join(s.name for s in servers)
    raise HerdrError(
        f"`{action}` needs one server, but {len(servers)} are configured ({names}). "
        f"Pass server=<name> — ids and agent names are scoped to a single Herdr server."
    )


def _as_records(data: Any, *keys: str) -> List[Dict[str, Any]]:
    """Pull a list of records out of a Herdr response, tolerantly.

    Herdr's exact envelope differs per command and per version; accept a bare
    list, or a mapping under any of *keys*.
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        # Single-record response (e.g. `agent get`).
        for key in keys:
            value = data.get(key)
            if isinstance(value, dict):
                return [value]
    return []


def _agent_state(record: Dict[str, Any]) -> str:
    for key in ("status", "state", "lifecycle_state"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
        if isinstance(value, dict):
            inner = value.get("state") or value.get("status")
            if isinstance(inner, str) and inner.strip():
                return inner.strip().lower()
    return "unknown"


def _agent_name(record: Dict[str, Any]) -> str:
    for key in ("name", "agent_name", "display_agent", "pane_id", "id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "?"


def _agent_kind(record: Dict[str, Any]) -> str:
    for key in ("agent", "kind", "agent_kind", "label"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_agent(record: Dict[str, Any], server: HerdrServer) -> Dict[str, Any]:
    """Flatten one Herdr agent record into a server-tagged summary.

    The original record is kept under ``raw`` so nothing Herdr reports is lost
    just because this mapping does not know about it yet.
    """
    state = _agent_state(record)
    return {
        "server": server.name,
        "machine": server.display,
        "name": _agent_name(record),
        "kind": _agent_kind(record),
        "state": state,
        "pane_id": record.get("pane_id") or record.get("pane") or "",
        "workspace": record.get("workspace") or record.get("workspace_label") or "",
        "cwd": record.get("cwd") or "",
        "summary": record.get("summary") or record.get("message") or "",
        "needs_attention": state in ("blocked", "done"),
        "raw": record,
    }


def _sort_agents(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        agents,
        key=lambda a: (
            STATE_PRIORITY.get(a.get("state", "unknown"), 9),
            a.get("server", ""),
            a.get("name", ""),
        ),
    )


def _collect_agents(
    servers: List[HerdrServer],
    *,
    timeout: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fan `agent list` across servers. Returns (agents, per-server errors)."""
    results = herdr_client.fan_out(servers, ["agent", "list"], timeout=timeout)
    agents: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    by_name = {s.name: s for s in servers}
    for result in results:
        server = by_name[result.server]
        if not result.ok:
            errors.append({"server": result.server, "error": result.error, "error_code": result.error_code})
            continue
        for record in _as_records(result.data, "agents", "agent"):
            agents.append(_normalize_agent(record, server))
    return _sort_agents(agents), errors


def _timeout_for_ms(timeout_ms: Optional[int]) -> Optional[int]:
    """Give the subprocess enough headroom to outlive Herdr's own wait."""
    if not timeout_ms:
        return None
    try:
        return int(int(timeout_ms) / 1000) + 15
    except (TypeError, ValueError):
        return None


def _int_or_none(raw: Any) -> Optional[int]:
    try:
        if raw is None or raw == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _fail(exc: Exception) -> str:
    if isinstance(exc, HerdrError):
        return tool_error(str(exc))
    return tool_error(f"Herdr tool failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# herdr_servers
# ---------------------------------------------------------------------------

HERDR_SERVERS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "status"],
            "description": "list = configured servers (no network). status = probe each one.",
        },
        "server": {
            "type": "string",
            "description": "Comma-separated server names, or omit/'all' for every configured server.",
        },
    },
    "required": ["action"],
}


def handle_herdr_servers(action: str = "list", server: str = "", **_: Any) -> str:
    try:
        if action == "list":
            servers = herdr_client.load_servers(include_disabled=True)
            return tool_result(
                success=True,
                action="list",
                count=len(servers),
                servers=[s.to_public_dict() for s in servers],
            )

        if action == "status":
            servers = _resolve(server)
            results = herdr_client.fan_out(servers, ["status", "server"])
            payload = []
            for result in results:
                entry: Dict[str, Any] = {"server": result.server, "reachable": result.ok}
                if result.ok:
                    entry["status"] = result.data
                else:
                    entry["error"] = result.error
                    if result.error_code:
                        entry["error_code"] = result.error_code
                payload.append(entry)
            return tool_result(
                success=True,
                action="status",
                reachable=sum(1 for p in payload if p["reachable"]),
                total=len(payload),
                servers=payload,
            )

        return tool_error(f"Unknown action {action!r}. Use 'list' or 'status'.")
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# herdr_agents
# ---------------------------------------------------------------------------

HERDR_AGENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "get", "read", "explain"],
            "description": (
                "list = every agent on every server, sorted by how much it needs you. "
                "get = one agent's record. read = its terminal output. "
                "explain = why Herdr classified its state that way."
            ),
        },
        "server": {"type": "string", "description": "Server name. Omit on 'list' for all servers; required for the rest when more than one is configured."},
        "target": {"type": "string", "description": "Agent name or the pane id hosting it (e.g. 'reviewer' or 'w1:p2')."},
        "state": {
            "type": "string",
            "enum": list(VALID_STATES),
            "description": "On 'list', keep only agents in this state.",
        },
        "source": {
            "type": "string",
            "enum": ["visible", "recent", "recent-unwrapped", "detection"],
            "description": "On 'read', which terminal snapshot to return. Default 'recent-unwrapped'.",
        },
        "lines": {"type": "integer", "description": "On 'read', how many terminal rows to return. Default 80."},
    },
    "required": ["action"],
}


def handle_herdr_agents(
    action: str = "list",
    server: str = "",
    target: str = "",
    state: str = "",
    source: str = "",
    lines: Any = None,
    **_: Any,
) -> str:
    try:
        if action == "list":
            servers = _resolve(server)
            agents, errors = _collect_agents(servers)
            if state:
                wanted = state.strip().lower()
                agents = [a for a in agents if a["state"] == wanted]
            payload: Dict[str, Any] = {
                "success": True,
                "action": "list",
                "count": len(agents),
                "servers_queried": [s.name for s in servers],
                "needs_attention": sum(1 for a in agents if a["needs_attention"]),
                "agents": agents,
            }
            if errors:
                payload["unreachable"] = errors
            return tool_result(payload)

        if not target:
            return tool_error(f"`{action}` requires a target (agent name or pane id).")

        srv = _require_single_server(server, action=f"herdr_agents {action}")

        if action == "get":
            result = herdr_client.call(srv, ["agent", "get", target])
        elif action == "read":
            args = ["agent", "read", target, "--source", source or "recent-unwrapped"]
            line_count = _int_or_none(lines)
            if line_count:
                args += ["--lines", str(line_count)]
            result = herdr_client.call(srv, args)
        elif action == "explain":
            result = herdr_client.call(srv, ["agent", "explain", target, "--json"])
        else:
            return tool_error(f"Unknown action {action!r}. Use 'list', 'get', 'read' or 'explain'.")

        if not result.ok:
            return tool_error(result.error, server=srv.name, error_code=result.error_code or None)
        return tool_result(success=True, action=action, server=srv.name, target=target, result=result.data)
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# herdr_control
# ---------------------------------------------------------------------------

HERDR_CONTROL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["prompt", "send_keys", "wait", "start", "focus", "rename", "notify"],
            "description": (
                "prompt = submit text to an agent. send_keys = press terminal keys "
                "(use this to answer a blocked approval dialog). wait = block until a state. "
                "start = launch an agent in an existing shell pane. focus / rename / notify."
            ),
        },
        "server": {"type": "string", "description": "Server name. Required when more than one is configured."},
        "target": {"type": "string", "description": "Agent name or pane id."},
        "text": {"type": "string", "description": "On 'prompt', the prompt text. On 'notify', the title."},
        "body": {"type": "string", "description": "On 'notify', the notification body."},
        "keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": "On 'send_keys', keys such as ['enter'], ['esc'], ['down','enter'], ['ctrl+c'].",
        },
        "wait": {"type": "boolean", "description": "On 'prompt', wait for the turn to settle."},
        "until": {
            "type": "array",
            "items": {"type": "string", "enum": list(VALID_STATES)},
            "description": "States that satisfy a wait. Defaults to idle/done/blocked.",
        },
        "timeout_ms": {"type": "integer", "description": "Wait timeout in milliseconds."},
        "kind": {
            "type": "string",
            "description": "On 'start', the agent kind: hermes, claude, codex, cursor, opencode, grok, gemini, droid, ...",
        },
        "pane": {"type": "string", "description": "On 'start', the existing shell pane to launch into (e.g. 'w1:p2')."},
        "name": {"type": "string", "description": "On 'start'/'rename', the agent name ([a-z][a-z0-9_-]{0,31})."},
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "On 'start', extra arguments passed through to the agent executable.",
        },
    },
    "required": ["action"],
}


def handle_herdr_control(
    action: str = "",
    server: str = "",
    target: str = "",
    text: str = "",
    body: str = "",
    keys: Any = None,
    wait: Any = False,
    until: Any = None,
    timeout_ms: Any = None,
    kind: str = "",
    pane: str = "",
    name: str = "",
    args: Any = None,
    **_: Any,
) -> str:
    try:
        srv = _require_single_server(server, action=f"herdr_control {action or '?'}")
        ms = _int_or_none(timeout_ms)
        subprocess_timeout = _timeout_for_ms(ms)
        until_list = [str(u).strip().lower() for u in (until or []) if str(u).strip()]
        for candidate in until_list:
            if candidate not in VALID_STATES:
                return tool_error(f"Invalid state {candidate!r}. Use one of: {', '.join(VALID_STATES)}.")

        if action == "prompt":
            if not target or not text:
                return tool_error("`prompt` requires target and text.")
            cmd = ["agent", "prompt", target, text]
            if wait:
                cmd.append("--wait")
                for candidate in until_list:
                    cmd += ["--until", candidate]
            elif until_list:
                return tool_error("`until` is only valid together with wait=true.")
            if ms:
                cmd += ["--timeout", str(ms)]
            result = herdr_client.call(srv, cmd, timeout=subprocess_timeout)

        elif action == "send_keys":
            key_list = [str(k).strip() for k in (keys or []) if str(k).strip()]
            if not target or not key_list:
                return tool_error("`send_keys` requires target and a non-empty keys list.")
            result = herdr_client.call(srv, ["agent", "send-keys", target, *key_list])

        elif action == "wait":
            if not target:
                return tool_error("`wait` requires a target.")
            cmd = ["agent", "wait", target]
            for candidate in until_list:
                cmd += ["--until", candidate]
            if ms:
                cmd += ["--timeout", str(ms)]
            result = herdr_client.call(srv, cmd, timeout=subprocess_timeout)

        elif action == "start":
            if not name or not kind or not pane:
                return tool_error("`start` requires name, kind and pane (an existing shell pane at its prompt).")
            cmd = ["agent", "start", name, "--kind", kind, "--pane", pane]
            if ms:
                cmd += ["--timeout", str(ms)]
            extra = [str(a) for a in (args or []) if str(a).strip()]
            if extra:
                cmd += ["--", *extra]
            result = herdr_client.call(srv, cmd, timeout=subprocess_timeout or 60)

        elif action == "focus":
            if not target:
                return tool_error("`focus` requires a target.")
            result = herdr_client.call(srv, ["agent", "focus", target])

        elif action == "rename":
            if not target or not name:
                return tool_error("`rename` requires target and name.")
            result = herdr_client.call(srv, ["agent", "rename", target, name])

        elif action == "notify":
            if not text:
                return tool_error("`notify` requires text (used as the title).")
            cmd = ["notification", "show", text]
            if body:
                cmd += ["--body", body]
            result = herdr_client.call(srv, cmd)

        else:
            return tool_error(
                f"Unknown action {action!r}. Use prompt, send_keys, wait, start, focus, rename or notify."
            )

        if not result.ok:
            return tool_error(result.error, server=srv.name, error_code=result.error_code or None)
        return tool_result(success=True, action=action, server=srv.name, result=result.data)
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# herdr_panes
# ---------------------------------------------------------------------------

HERDR_PANES_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["workspaces", "list", "split", "run", "read", "wait_output", "close"],
            "description": (
                "workspaces = workspace list. list = panes. split = make a new pane. "
                "run = run a shell command in a pane. read = pane output. "
                "wait_output = block until text/regex appears. close = close a pane."
            ),
        },
        "server": {"type": "string", "description": "Server name. Required for everything but 'workspaces'/'list' when more than one is configured."},
        "pane": {"type": "string", "description": "Pane id, e.g. 'w1:p2'."},
        "command": {"type": "string", "description": "On 'run', the shell command to submit."},
        "direction": {
            "type": "string",
            "enum": ["left", "right", "up", "down"],
            "description": "On 'split', which way to split. Default 'right'.",
        },
        "cwd": {"type": "string", "description": "On 'split', working directory for the new pane."},
        "pattern": {"type": "string", "description": "On 'wait_output', literal text to wait for."},
        "regex": {"type": "string", "description": "On 'wait_output', a Rust regex to wait for."},
        "lines": {"type": "integer", "description": "On 'read', terminal rows to return."},
        "timeout_ms": {"type": "integer", "description": "On 'wait_output', timeout in milliseconds."},
    },
    "required": ["action"],
}


def handle_herdr_panes(
    action: str = "",
    server: str = "",
    pane: str = "",
    command: str = "",
    direction: str = "",
    cwd: str = "",
    pattern: str = "",
    regex: str = "",
    lines: Any = None,
    timeout_ms: Any = None,
    **_: Any,
) -> str:
    try:
        if action in ("workspaces", "list"):
            servers = _resolve(server)
            sub = ["workspace", "list"] if action == "workspaces" else ["pane", "list"]
            results = herdr_client.fan_out(servers, sub)
            payload = []
            errors = []
            for result in results:
                if not result.ok:
                    errors.append({"server": result.server, "error": result.error})
                    continue
                key = "workspaces" if action == "workspaces" else "panes"
                for record in _as_records(result.data, key, "workspace", "pane"):
                    record = dict(record)
                    record["server"] = result.server
                    payload.append(record)
            out: Dict[str, Any] = {"success": True, "action": action, "count": len(payload), action: payload}
            if errors:
                out["unreachable"] = errors
            return tool_result(out)

        srv = _require_single_server(server, action=f"herdr_panes {action or '?'}")
        ms = _int_or_none(timeout_ms)

        if action == "split":
            cmd = ["pane", "split"]
            if pane:
                cmd.append(pane)
            else:
                cmd.append("--current")
            cmd += ["--direction", direction or "right"]
            if cwd:
                cmd += ["--cwd", cwd]
            result = herdr_client.call(srv, cmd)

        elif action == "run":
            if not pane or not command:
                return tool_error("`run` requires pane and command.")
            result = herdr_client.call(srv, ["pane", "run", pane, command])

        elif action == "read":
            if not pane:
                return tool_error("`read` requires a pane.")
            cmd = ["pane", "read", pane, "--source", "recent"]
            line_count = _int_or_none(lines)
            if line_count:
                cmd += ["--lines", str(line_count)]
            result = herdr_client.call(srv, cmd)

        elif action == "wait_output":
            if not pane or not (pattern or regex):
                return tool_error("`wait_output` requires pane and either pattern or regex.")
            cmd = ["pane", "wait-output", pane]
            if regex:
                cmd += ["--regex", regex]
            else:
                cmd.append(pattern)
            if ms:
                cmd += ["--timeout", str(ms)]
            result = herdr_client.call(srv, cmd, timeout=_timeout_for_ms(ms))

        elif action == "close":
            if not pane:
                return tool_error("`close` requires a pane.")
            result = herdr_client.call(srv, ["pane", "close", pane])

        else:
            return tool_error(f"Unknown action {action!r}.")

        if not result.ok:
            return tool_error(result.error, server=srv.name, error_code=result.error_code or None)
        return tool_result(success=True, action=action, server=srv.name, result=result.data)
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# herdr_board — the compact, merged control surface
# ---------------------------------------------------------------------------

HERDR_BOARD_SCHEMA = {
    "type": "object",
    "properties": {
        "width": {"type": "integer", "description": "Characters per line. Default 40, sized for a heads-up display."},
        "max_rows": {"type": "integer", "description": "Maximum agent rows. Default 6."},
        "server": {"type": "string", "description": "Restrict to these servers. Omit for all."},
        "attention_only": {"type": "boolean", "description": "Show only agents that are blocked or done."},
    },
}

_STATE_GLYPH = {"blocked": "!", "done": "*", "working": ">", "idle": "-", "unknown": "?"}


def _truncate(value: str, width: int) -> str:
    value = " ".join(str(value).split())
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def _board_lines(agents: List[Dict[str, Any]], *, width: int, max_rows: int) -> List[str]:
    """Render agent rows as fixed-width lines for a small display.

    Layout is ``<glyph> <machine>/<agent> <STATE>`` with the machine kept
    because the whole point of the board is that it spans machines.
    """
    lines: List[str] = []
    for agent in agents[:max_rows]:
        glyph = _STATE_GLYPH.get(agent["state"], "?")
        state = agent["state"].upper()
        # Reserve room for "<glyph> " and " <STATE>".
        label_width = max(4, width - len(state) - 3)
        label = _truncate(f"{agent['machine']}/{agent['name']}", label_width)
        pad = " " * max(1, width - len(glyph) - 1 - len(label) - len(state))
        lines.append(f"{glyph} {label}{pad}{state}"[:width])
    return lines


def handle_herdr_board(
    width: Any = 40,
    max_rows: Any = 6,
    server: str = "",
    attention_only: Any = False,
    **_: Any,
) -> str:
    try:
        width = max(20, min(_int_or_none(width) or 40, 120))
        max_rows = max(1, min(_int_or_none(max_rows) or 6, 40))
        servers = _resolve(server)
        agents, errors = _collect_agents(servers)

        if attention_only:
            agents = [a for a in agents if a["needs_attention"]]

        blocked = [a for a in agents if a["state"] == "blocked"]
        working = [a for a in agents if a["state"] == "working"]
        done = [a for a in agents if a["state"] == "done"]

        if agents:
            headline = f"{len(blocked)} blocked  {len(working)} working  {len(done)} done"
        elif errors:
            headline = "no agents (some servers unreachable)"
        else:
            headline = "no agents running"

        lines = _board_lines(agents, width=width, max_rows=max_rows)
        overflow = max(0, len(agents) - max_rows)
        if overflow:
            lines.append(_truncate(f"+{overflow} more", width))
        for err in errors:
            lines.append(_truncate(f"! {err['server']} unreachable", width))

        return tool_result(
            success=True,
            headline=_truncate(headline, width),
            lines=lines,
            display_text="\n".join([_truncate(headline, width), *lines]),
            counts={
                "total": len(agents),
                "blocked": len(blocked),
                "working": len(working),
                "done": len(done),
                "idle": sum(1 for a in agents if a["state"] == "idle"),
            },
            servers_queried=[s.name for s in servers],
            unreachable=errors or None,
            agents=agents,
        )
    except Exception as exc:
        return _fail(exc)
