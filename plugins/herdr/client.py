"""Multi-server client for Herdr (https://github.com/herdrdev/herdr).

Herdr is a terminal multiplexer built for coding agents: every pane is marked
``working``, ``blocked``, or ``idle``, and its CLI talks to a running server
over a local socket. Herdr already knows what Hermes is — it ships a
``hermes`` detection manifest, an ``Agent::Hermes`` kind, and
``herdr integration install hermes``. This module is the other direction:
it lets Hermes drive Herdr.

Why a *multi-server* client
---------------------------
Herdr's own multi-machine feature (``herdr machine add``) merges several hosts
into one client window, but it is documented as Linux/macOS clients talking to
Linux/macOS servers, and "Native Windows servers are not supported as SSH
targets." A Windows Herdr server therefore cannot join that merged view.

So this client does the merging itself, one rung up: it fans the Herdr CLI out
across every configured server and returns a single combined result. Each
server is reached either locally or by running the remote ``herdr`` binary over
SSH, which works regardless of the remote OS. That merged list is what makes a
single control surface -- a phone, a terminal, or a pair of smart glasses
talking to Hermes -- able to see and steer every machine at once.

Configuration
-------------
Servers come from ``config.yaml``::

    herdr:
      servers:
        - name: local
          transport: local
        - name: khadas
          transport: ssh
          target: perry-lee@khadas
          shell_quoting: windows      # Windows OpenSSH target
        - name: mac-mini
          transport: ssh
          target: perry@mac-mini

or from the ``HERDR_SERVERS`` environment variable holding the same list as
JSON. With neither set, a single implicit ``local`` server is used, so the
plugin does something sensible on a machine that just runs ``herdr`` directly.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Herdr's CLI is a local socket client: every call is a short round trip to an
# already-running server, so these stay small. SSH adds a connect, hence the
# separate, larger default for remote servers.
DEFAULT_LOCAL_TIMEOUT = 20
DEFAULT_SSH_TIMEOUT = 45
DEFAULT_SSH_CONNECT_TIMEOUT = 10

# Cap on how long a caller may ask a single Herdr call to block. `agent wait`
# and `agent prompt --wait` can legitimately wait minutes; anything past this
# is almost certainly a mistake and would wedge a Hermes turn.
MAX_TIMEOUT = 900


class HerdrError(RuntimeError):
    """A Herdr call failed in a way the caller should surface, not swallow."""


@dataclass
class HerdrServer:
    """One Herdr server Hermes can reach."""

    name: str
    transport: str = "local"          # "local" | "ssh"
    target: str = ""                  # ssh target, e.g. "perry@mac-mini"
    bin: str = "herdr"                # herdr executable on that host
    label: str = ""                   # human label for display surfaces
    enabled: bool = True
    # POSIX quoting is right for Linux/macOS SSH targets. Windows OpenSSH runs
    # the command through cmd.exe, where POSIX single quotes are literal
    # characters -- use "windows" for those hosts.
    shell_quoting: str = "posix"
    ssh_options: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout: Optional[int] = None

    @property
    def display(self) -> str:
        return self.label or self.name

    @property
    def is_remote(self) -> bool:
        return self.transport == "ssh"

    def default_timeout(self) -> int:
        if self.timeout:
            return int(self.timeout)
        return DEFAULT_SSH_TIMEOUT if self.is_remote else DEFAULT_LOCAL_TIMEOUT

    def to_public_dict(self) -> Dict[str, Any]:
        """Describe the server without leaking anything credential-shaped.

        ``target`` is an SSH destination, which is host/user routing rather
        than a secret, but nothing here ever includes keys or passwords --
        authentication stays entirely with OpenSSH.
        """
        return {
            "name": self.name,
            "label": self.display,
            "transport": self.transport,
            "target": self.target,
            "enabled": self.enabled,
        }


@dataclass
class HerdrResult:
    """Outcome of one Herdr CLI call against one server."""

    server: str
    ok: bool
    data: Any = None
    error: str = ""
    error_code: str = ""
    exit_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"server": self.server, "ok": self.ok}
        if self.ok:
            out["result"] = self.data
        else:
            out["error"] = self.error
            if self.error_code:
                out["error_code"] = self.error_code
            if self.exit_code is not None:
                out["exit_code"] = self.exit_code
        return out


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _coerce_server(raw: Any, *, index: int) -> Optional[HerdrServer]:
    """Build a HerdrServer from a config entry, or None if unusable."""
    if isinstance(raw, str):
        # Shorthand: a bare string is an SSH target whose name is the target.
        raw = {"name": raw, "transport": "ssh", "target": raw}
    if not isinstance(raw, dict):
        logger.warning("herdr: ignoring server entry %d — expected mapping, got %s", index, type(raw).__name__)
        return None

    name = str(raw.get("name") or raw.get("target") or "").strip()
    if not name:
        logger.warning("herdr: ignoring server entry %d — no name or target", index)
        return None

    transport = str(raw.get("transport") or ("ssh" if raw.get("target") else "local")).strip().lower()
    if transport not in {"local", "ssh"}:
        logger.warning("herdr: server %r has unsupported transport %r — skipping", name, transport)
        return None

    target = str(raw.get("target") or "").strip()
    if transport == "ssh" and not target:
        logger.warning("herdr: server %r uses ssh transport but has no target — skipping", name)
        return None

    quoting = str(raw.get("shell_quoting") or "posix").strip().lower()
    if quoting not in {"posix", "windows"}:
        logger.warning("herdr: server %r has unknown shell_quoting %r — using posix", name, quoting)
        quoting = "posix"

    ssh_options = raw.get("ssh_options") or []
    if not isinstance(ssh_options, list):
        ssh_options = []

    env = raw.get("env") or {}
    if not isinstance(env, dict):
        env = {}

    timeout_raw = raw.get("timeout")
    try:
        timeout = int(timeout_raw) if timeout_raw else None
    except (TypeError, ValueError):
        timeout = None

    return HerdrServer(
        name=name,
        transport=transport,
        target=target,
        bin=str(raw.get("bin") or raw.get("binary") or "herdr").strip() or "herdr",
        label=str(raw.get("label") or "").strip(),
        enabled=raw.get("enabled", True) is not False,
        shell_quoting=quoting,
        ssh_options=[str(o) for o in ssh_options],
        env={str(k): str(v) for k, v in env.items()},
        timeout=timeout,
    )


def _raw_server_entries() -> List[Any]:
    """Read the configured server list from env first, then config.yaml."""
    env_raw = os.environ.get("HERDR_SERVERS", "").strip()
    if env_raw:
        try:
            parsed = json.loads(env_raw)
            if isinstance(parsed, list):
                return parsed
            logger.warning("herdr: HERDR_SERVERS must be a JSON list — ignoring")
        except json.JSONDecodeError as exc:
            logger.warning("herdr: HERDR_SERVERS is not valid JSON (%s) — ignoring", exc)

    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        section = cfg.get("herdr") or {}
        if isinstance(section, dict):
            servers = section.get("servers")
            if isinstance(servers, list):
                return servers
    except Exception as exc:  # config is optional; never break tool registration
        logger.debug("herdr: could not read config.yaml (%s)", exc)
    return []


def load_servers(*, include_disabled: bool = False) -> List[HerdrServer]:
    """Return the configured Herdr servers, defaulting to a single local one."""
    servers: List[HerdrServer] = []
    seen: set = set()
    for index, raw in enumerate(_raw_server_entries()):
        server = _coerce_server(raw, index=index)
        if server is None:
            continue
        if server.name in seen:
            logger.warning("herdr: duplicate server name %r — keeping the first", server.name)
            continue
        seen.add(server.name)
        servers.append(server)

    if not servers:
        servers = [HerdrServer(name="local", transport="local")]

    if include_disabled:
        return servers
    return [s for s in servers if s.enabled]


def resolve_servers(selector: Optional[str] = None) -> List[HerdrServer]:
    """Resolve a server selector to concrete servers.

    ``None``/``""``/``"all"`` means every enabled server -- that is what makes
    the tools a merged control surface by default. Otherwise the selector is a
    comma-separated list of server names.
    """
    servers = load_servers()
    if not selector or selector.strip().lower() in {"all", "*"}:
        return servers

    wanted = [part.strip() for part in selector.split(",") if part.strip()]
    by_name = {s.name: s for s in servers}
    resolved: List[HerdrServer] = []
    missing: List[str] = []
    for name in wanted:
        if name in by_name:
            resolved.append(by_name[name])
        else:
            missing.append(name)
    if missing:
        known = ", ".join(sorted(by_name)) or "none configured"
        raise HerdrError(f"Unknown Herdr server(s): {', '.join(missing)}. Configured servers: {known}")
    return resolved


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def _quote_windows(args: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(args))


def _quote_posix(args: Sequence[str]) -> str:
    import shlex

    return shlex.join(list(args))


def build_command(server: HerdrServer, args: Sequence[str]) -> List[str]:
    """Build the argv that runs ``herdr <args>`` against *server*."""
    herdr_argv = [server.bin, *[str(a) for a in args]]
    if not server.is_remote:
        return herdr_argv

    if server.shell_quoting == "windows":
        remote = _quote_windows(herdr_argv)
    else:
        remote = _quote_posix(herdr_argv)

    ssh_argv = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={DEFAULT_SSH_CONNECT_TIMEOUT}"]
    for option in server.ssh_options:
        # Accept both "-o Foo=bar" style pairs already split by the user and
        # bare "Foo=bar" values.
        if option.startswith("-"):
            ssh_argv.append(option)
        else:
            ssh_argv.extend(["-o", option])
    ssh_argv.append(server.target)
    ssh_argv.append(remote)
    return ssh_argv


def _extract_error(payload: Any) -> Tuple[str, str]:
    """Pull (message, code) out of a Herdr JSON error response."""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or "herdr error"), str(err.get("code") or "")
        if isinstance(err, str):
            return err, str(payload.get("code") or "")
    return "", ""


def _parse_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some commands are human-readable by default; hand the text back so
        # the caller can still show it.
        return None


def call(
    server: HerdrServer,
    args: Sequence[str],
    *,
    timeout: Optional[int] = None,
) -> HerdrResult:
    """Run one Herdr CLI command against *server* and parse its JSON reply."""
    effective = timeout or server.default_timeout()
    effective = max(1, min(int(effective), MAX_TIMEOUT))
    argv = build_command(server, args)

    env = os.environ.copy()
    env.update(server.env)
    # Herdr commands run from inside a Herdr pane inherit that pane's socket.
    # Hermes may itself be running in a pane, which would silently retarget
    # every "local" call at the pane's own server. Clear the pane-scoped hints
    # so server selection stays explicit, unless the server opted into them.
    for leaked in ("HERDR_PANE_ID", "HERDR_ENV"):
        if leaked not in server.env:
            env.pop(leaked, None)

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=effective,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        missing = "ssh" if server.is_remote else server.bin
        return HerdrResult(
            server=server.name,
            ok=False,
            error=f"`{missing}` not found on this machine.",
            error_code="not_installed",
        )
    except subprocess.TimeoutExpired:
        return HerdrResult(
            server=server.name,
            ok=False,
            error=f"Herdr call timed out after {effective}s: herdr {' '.join(str(a) for a in args)}",
            error_code="timeout",
        )
    except OSError as exc:
        return HerdrResult(server=server.name, ok=False, error=f"Could not run herdr: {exc}", error_code="spawn_failed")

    stdout_payload = _parse_json(proc.stdout)
    stderr_payload = _parse_json(proc.stderr)

    if proc.returncode == 0:
        data = stdout_payload
        if data is None:
            # Human-readable command (e.g. `herdr status`): return the text.
            text = (proc.stdout or "").strip()
            data = {"text": text} if text else {}
        # Herdr wraps successful payloads in {"result": ...}; unwrap for callers.
        if isinstance(data, dict) and "result" in data and len(data) <= 2:
            data = data["result"]
        return HerdrResult(server=server.name, ok=True, data=data, exit_code=0)

    message, code = _extract_error(stderr_payload or stdout_payload)
    if not message:
        message = (proc.stderr or proc.stdout or "").strip() or f"herdr exited {proc.returncode}"
    if server.is_remote and proc.returncode == 255 and not code:
        # 255 is ssh's own failure code, not herdr's.
        code = "ssh_failed"
        message = f"SSH to {server.target} failed: {message}"
    return HerdrResult(
        server=server.name,
        ok=False,
        error=message,
        error_code=code,
        exit_code=proc.returncode,
    )


def fan_out(
    servers: Sequence[HerdrServer],
    args: Sequence[str],
    *,
    timeout: Optional[int] = None,
) -> List[HerdrResult]:
    """Run the same command on every server concurrently, preserving order.

    One unreachable machine must not hide the others -- each result carries its
    own ok/error, exactly like Herdr's own "a lost connection to one machine
    does not disconnect the others" behaviour.
    """
    if not servers:
        return []
    if len(servers) == 1:
        return [call(servers[0], args, timeout=timeout)]

    with ThreadPoolExecutor(max_workers=min(len(servers), 8)) as pool:
        futures = [pool.submit(call, server, args, timeout=timeout) for server in servers]
        return [future.result() for future in futures]


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def _herdr_binary_present() -> bool:
    """True when a local `herdr` executable exists."""
    for server in load_servers():
        if not server.is_remote and shutil.which(server.bin):
            return True
    return False


def is_available() -> bool:
    """Gate for tool dispatch.

    Registration is unconditional so the tools always show up in
    ``hermes tools``; this decides whether they can actually run. A local
    ``herdr`` binary, or any configured remote server plus an ``ssh`` client,
    counts as available. Reachability itself is not probed here -- that would
    put an SSH round trip in front of every tool listing.
    """
    if _herdr_binary_present():
        return True
    servers = load_servers()
    if any(s.is_remote for s in servers) and shutil.which("ssh"):
        return True
    return False
