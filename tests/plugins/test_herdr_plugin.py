"""Tests for the Herdr integration plugin (plugins/herdr).

These exercise the parts that must be right without a Herdr server present:
server configuration, command construction (including Windows SSH targets),
response parsing, cross-server merging, and the compact board renderer.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from plugins.herdr import client as herdr_client
from plugins.herdr import tools as herdr_tools
from plugins.herdr.client import HerdrError, HerdrServer


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def test_defaults_to_single_local_server(monkeypatch):
    monkeypatch.delenv("HERDR_SERVERS", raising=False)
    with patch.object(herdr_client, "_raw_server_entries", return_value=[]):
        servers = herdr_client.load_servers()
    assert [s.name for s in servers] == ["local"]
    assert servers[0].transport == "local"


def test_servers_load_from_env(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "khadas", "transport": "ssh", "target": "p@khadas", "shell_quoting": "windows"},
            {"name": "mac-mini", "transport": "ssh", "target": "p@mac-mini"},
        ]),
    )
    servers = herdr_client.load_servers()
    assert [s.name for s in servers] == ["khadas", "mac-mini"]
    assert servers[0].shell_quoting == "windows"
    assert servers[1].is_remote


def test_string_shorthand_is_an_ssh_target(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps(["build-box"]))
    servers = herdr_client.load_servers()
    assert servers[0].name == "build-box"
    assert servers[0].transport == "ssh"
    assert servers[0].target == "build-box"


def test_invalid_entries_are_skipped_not_fatal(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "good", "transport": "local"},
            {"name": "no-target", "transport": "ssh"},      # ssh without target
            {"transport": "local"},                          # no name
            {"name": "bad-transport", "transport": "telnet"},
        ]),
    )
    assert [s.name for s in herdr_client.load_servers()] == ["good"]


def test_disabled_servers_are_excluded_unless_requested(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "on", "transport": "local"},
            {"name": "off", "transport": "ssh", "target": "x", "enabled": False},
        ]),
    )
    assert [s.name for s in herdr_client.load_servers()] == ["on"]
    assert [s.name for s in herdr_client.load_servers(include_disabled=True)] == ["on", "off"]


def test_public_dict_carries_no_credentials(monkeypatch):
    server = HerdrServer(name="k", transport="ssh", target="p@k", env={"SECRET": "x"})
    assert "SECRET" not in json.dumps(server.to_public_dict())


def test_resolve_unknown_server_raises(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    with pytest.raises(HerdrError) as exc:
        herdr_client.resolve_servers("nope")
    assert "nope" in str(exc.value)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def test_local_command_is_plain_argv():
    server = HerdrServer(name="local")
    assert herdr_client.build_command(server, ["agent", "list"]) == ["herdr", "agent", "list"]


def test_posix_ssh_quotes_with_single_quotes():
    server = HerdrServer(name="m", transport="ssh", target="p@mac")
    argv = herdr_client.build_command(server, ["agent", "prompt", "rev", "ship it"])
    assert argv[0] == "ssh"
    assert argv[-2] == "p@mac"
    assert argv[-1] == "herdr agent prompt rev 'ship it'"


def test_windows_ssh_quotes_with_double_quotes():
    """Windows OpenSSH runs through cmd.exe, where POSIX quotes are literal."""
    server = HerdrServer(name="k", transport="ssh", target="p@khadas", shell_quoting="windows")
    argv = herdr_client.build_command(server, ["agent", "prompt", "rev", "ship it"])
    assert argv[-1] == 'herdr agent prompt rev "ship it"'
    assert "'ship it'" not in argv[-1]


def test_custom_binary_and_ssh_options_are_used():
    server = HerdrServer(
        name="m", transport="ssh", target="p@mac",
        bin="/opt/homebrew/bin/herdr", ssh_options=["Port=2222", "-4"],
    )
    argv = herdr_client.build_command(server, ["status"])
    assert "-4" in argv
    assert "Port=2222" in argv
    assert argv[-1].startswith("/opt/homebrew/bin/herdr")


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------

def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_successful_json_result_is_unwrapped():
    payload = json.dumps({"result": {"agents": [{"name": "rev"}]}})
    with patch("subprocess.run", return_value=_completed(stdout=payload)):
        result = herdr_client.call(HerdrServer(name="local"), ["agent", "list"])
    assert result.ok
    assert result.data == {"agents": [{"name": "rev"}]}


def test_non_json_output_is_returned_as_text():
    with patch("subprocess.run", return_value=_completed(stdout="server running\n")):
        result = herdr_client.call(HerdrServer(name="local"), ["status"])
    assert result.ok
    assert result.data == {"text": "server running"}


def test_json_error_on_stderr_is_surfaced_with_code():
    err = json.dumps({"error": {"code": "agent_blocked", "message": "agent is blocked"}})
    with patch("subprocess.run", return_value=_completed(stderr=err, returncode=1)):
        result = herdr_client.call(HerdrServer(name="local"), ["agent", "prompt", "rev", "hi"])
    assert not result.ok
    assert result.error_code == "agent_blocked"
    assert "blocked" in result.error


def test_ssh_exit_255_is_reported_as_ssh_failure():
    with patch("subprocess.run", return_value=_completed(stderr="Permission denied", returncode=255)):
        result = herdr_client.call(HerdrServer(name="k", transport="ssh", target="p@k"), ["agent", "list"])
    assert not result.ok
    assert result.error_code == "ssh_failed"
    assert "p@k" in result.error


def test_missing_binary_is_reported_not_raised():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = herdr_client.call(HerdrServer(name="local"), ["agent", "list"])
    assert not result.ok
    assert result.error_code == "not_installed"


def test_timeout_is_reported_not_raised():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="herdr", timeout=5)):
        result = herdr_client.call(HerdrServer(name="local"), ["agent", "wait", "rev"])
    assert not result.ok
    assert result.error_code == "timeout"


def test_timeout_is_clamped_to_max():
    captured = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _completed(stdout="{}")

    with patch("subprocess.run", side_effect=fake_run):
        herdr_client.call(HerdrServer(name="local"), ["agent", "wait", "rev"], timeout=10_000)
    assert captured["timeout"] == herdr_client.MAX_TIMEOUT


def test_pane_scoped_env_is_not_inherited(monkeypatch):
    """Hermes may itself run inside a Herdr pane; that must not retarget calls."""
    monkeypatch.setenv("HERDR_PANE_ID", "w9:p9")
    monkeypatch.setenv("HERDR_ENV", "1")
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return _completed(stdout="{}")

    with patch("subprocess.run", side_effect=fake_run):
        herdr_client.call(HerdrServer(name="local"), ["agent", "list"])
    assert "HERDR_PANE_ID" not in captured["env"]
    assert "HERDR_ENV" not in captured["env"]


# ---------------------------------------------------------------------------
# Merging across servers
# ---------------------------------------------------------------------------

def test_fan_out_preserves_order_and_isolates_failures():
    servers = [
        HerdrServer(name="a", transport="ssh", target="a"),
        HerdrServer(name="b", transport="ssh", target="b"),
    ]

    def fake_call(server, args, timeout=None):
        if server.name == "a":
            return herdr_client.HerdrResult(server="a", ok=True, data={"agents": []})
        return herdr_client.HerdrResult(server="b", ok=False, error="down", error_code="ssh_failed")

    with patch.object(herdr_client, "call", side_effect=fake_call):
        results = herdr_client.fan_out(servers, ["agent", "list"])
    assert [r.server for r in results] == ["a", "b"]
    assert results[0].ok and not results[1].ok


def test_agents_list_merges_and_tags_each_server(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "khadas", "transport": "ssh", "target": "k"},
            {"name": "mac-mini", "transport": "ssh", "target": "m"},
        ]),
    )

    def fake_call(server, args, timeout=None):
        if server.name == "khadas":
            return herdr_client.HerdrResult(
                server="khadas", ok=True,
                data={"agents": [{"name": "builder", "agent": "hermes", "status": "working"}]},
            )
        return herdr_client.HerdrResult(
            server="mac-mini", ok=True,
            data={"agents": [{"name": "reviewer", "agent": "claude", "status": "blocked"}]},
        )

    with patch.object(herdr_client, "call", side_effect=fake_call):
        payload = json.loads(herdr_tools.handle_herdr_agents(action="list"))

    assert payload["count"] == 2
    # Blocked sorts ahead of working: the board leads with what needs a human.
    assert payload["agents"][0]["name"] == "reviewer"
    assert payload["agents"][0]["server"] == "mac-mini"
    assert payload["agents"][1]["server"] == "khadas"
    assert payload["needs_attention"] == 1


def test_unreachable_server_does_not_hide_the_others(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "up", "transport": "local"},
            {"name": "down", "transport": "ssh", "target": "d"},
        ]),
    )

    def fake_call(server, args, timeout=None):
        if server.name == "up":
            return herdr_client.HerdrResult(server="up", ok=True, data={"agents": [{"name": "a", "status": "idle"}]})
        return herdr_client.HerdrResult(server="down", ok=False, error="ssh down", error_code="ssh_failed")

    with patch.object(herdr_client, "call", side_effect=fake_call):
        payload = json.loads(herdr_tools.handle_herdr_agents(action="list"))

    assert payload["count"] == 1
    assert payload["unreachable"][0]["server"] == "down"


def test_bare_list_response_shape_is_tolerated():
    """Herdr's envelope varies by command; a bare list must still parse."""
    assert herdr_tools._as_records([{"name": "x"}], "agents") == [{"name": "x"}]
    assert herdr_tools._as_records({"agents": [{"name": "y"}]}, "agents") == [{"name": "y"}]
    assert herdr_tools._as_records({"agent": {"name": "z"}}, "agents", "agent") == [{"name": "z"}]
    assert herdr_tools._as_records("nonsense", "agents") == []


def test_state_is_read_from_several_field_shapes():
    assert herdr_tools._agent_state({"status": "Blocked"}) == "blocked"
    assert herdr_tools._agent_state({"state": "working"}) == "working"
    assert herdr_tools._agent_state({"status": {"state": "idle"}}) == "idle"
    assert herdr_tools._agent_state({}) == "unknown"


# ---------------------------------------------------------------------------
# Ambiguity guards
# ---------------------------------------------------------------------------

def test_mutating_call_refuses_ambiguous_multi_server(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "khadas", "transport": "ssh", "target": "k"},
            {"name": "mac-mini", "transport": "ssh", "target": "m"},
        ]),
    )
    payload = json.loads(herdr_tools.handle_herdr_control(action="focus", target="reviewer"))
    assert "error" in payload
    assert "khadas" in payload["error"] and "mac-mini" in payload["error"]


def test_naming_the_server_resolves_the_ambiguity(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "khadas", "transport": "ssh", "target": "k"},
            {"name": "mac-mini", "transport": "ssh", "target": "m"},
        ]),
    )
    with patch.object(
        herdr_client, "call",
        return_value=herdr_client.HerdrResult(server="mac-mini", ok=True, data={"ok": True}),
    ):
        payload = json.loads(
            herdr_tools.handle_herdr_control(action="focus", target="reviewer", server="mac-mini")
        )
    assert payload["success"] is True
    assert payload["server"] == "mac-mini"


def test_until_without_wait_is_rejected(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    payload = json.loads(
        herdr_tools.handle_herdr_control(action="prompt", target="rev", text="hi", until=["idle"])
    )
    assert "error" in payload


def test_invalid_until_state_is_rejected(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    payload = json.loads(
        herdr_tools.handle_herdr_control(action="wait", target="rev", until=["finished"])
    )
    assert "error" in payload
    assert "finished" in payload["error"]


def test_prompt_builds_the_documented_command(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    captured = {}

    def fake_call(server, args, timeout=None):
        captured["args"] = list(args)
        return herdr_client.HerdrResult(server=server.name, ok=True, data={})

    with patch.object(herdr_client, "call", side_effect=fake_call):
        herdr_tools.handle_herdr_control(
            action="prompt", target="rev", text="review the diff",
            wait=True, until=["idle", "blocked"], timeout_ms=120000,
        )
    assert captured["args"] == [
        "agent", "prompt", "rev", "review the diff",
        "--wait", "--until", "idle", "--until", "blocked", "--timeout", "120000",
    ]


def test_send_keys_requires_keys(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    payload = json.loads(herdr_tools.handle_herdr_control(action="send_keys", target="rev", keys=[]))
    assert "error" in payload


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

def _agent(server, name, state):
    return {
        "server": server, "machine": server, "name": name, "kind": "hermes",
        "state": state, "pane_id": "", "workspace": "", "cwd": "", "summary": "",
        "needs_attention": state in ("blocked", "done"), "raw": {},
    }


def test_board_lines_respect_width():
    agents = [_agent("mac-mini", "a-very-long-agent-name-here", "blocked")]
    lines = herdr_tools._board_lines(agents, width=32, max_rows=6)
    assert len(lines) == 1
    assert len(lines[0]) <= 32
    assert lines[0].endswith("BLOCKED")


def test_board_merges_servers_and_counts(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "khadas", "transport": "ssh", "target": "k"},
            {"name": "mac-mini", "transport": "ssh", "target": "m"},
        ]),
    )

    def fake_call(server, args, timeout=None):
        if server.name == "khadas":
            return herdr_client.HerdrResult(
                server="khadas", ok=True,
                data={"agents": [{"name": "builder", "status": "working"}]},
            )
        return herdr_client.HerdrResult(
            server="mac-mini", ok=True,
            data={"agents": [{"name": "reviewer", "status": "blocked"}]},
        )

    with patch.object(herdr_client, "call", side_effect=fake_call):
        payload = json.loads(herdr_tools.handle_herdr_board(width=40, max_rows=6))

    assert payload["counts"] == {"total": 2, "blocked": 1, "working": 1, "done": 0, "idle": 0}
    assert "1 blocked" in payload["headline"]
    assert any("mac-mini/reviewer" in line for line in payload["lines"])
    assert any("khadas/builder" in line for line in payload["lines"])
    assert payload["display_text"].count("\n") == 2


def test_board_reports_overflow_and_unreachable(monkeypatch):
    monkeypatch.setenv(
        "HERDR_SERVERS",
        json.dumps([
            {"name": "up", "transport": "local"},
            {"name": "down", "transport": "ssh", "target": "d"},
        ]),
    )
    many = [{"name": f"a{i}", "status": "idle"} for i in range(5)]

    def fake_call(server, args, timeout=None):
        if server.name == "up":
            return herdr_client.HerdrResult(server="up", ok=True, data={"agents": many})
        return herdr_client.HerdrResult(server="down", ok=False, error="x", error_code="ssh_failed")

    with patch.object(herdr_client, "call", side_effect=fake_call):
        payload = json.loads(herdr_tools.handle_herdr_board(width=40, max_rows=2))

    assert any("+3 more" in line for line in payload["lines"])
    assert any("down unreachable" in line for line in payload["lines"])


def test_board_attention_only_filters(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    data = {"agents": [{"name": "a", "status": "idle"}, {"name": "b", "status": "blocked"}]}
    with patch.object(
        herdr_client, "call",
        return_value=herdr_client.HerdrResult(server="local", ok=True, data=data),
    ):
        payload = json.loads(herdr_tools.handle_herdr_board(attention_only=True))
    assert payload["counts"]["total"] == 1
    assert payload["agents"][0]["name"] == "b"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_adds_every_declared_tool():
    import yaml

    from plugins import herdr as herdr_plugin

    registered = []

    class Ctx:
        def register_tool(self, **kw):
            registered.append(kw)

    herdr_plugin.register(Ctx())
    names = [entry["name"] for entry in registered]

    manifest = yaml.safe_load(open("plugins/herdr/plugin.yaml"))
    assert sorted(names) == sorted(manifest["provides_tools"])
    assert all(entry["toolset"] == "herdr" for entry in registered)
    assert all(entry["check_fn"] is herdr_tools.check_herdr_available for entry in registered)


def test_toolset_lists_the_same_tools():
    import yaml

    from toolsets import TOOLSETS

    manifest = yaml.safe_load(open("plugins/herdr/plugin.yaml"))
    assert sorted(TOOLSETS["herdr"]["tools"]) == sorted(manifest["provides_tools"])


def test_herdr_is_reachable_from_the_core_toolsets():
    """The gateway/API path is how the glasses reach these tools."""
    from toolsets import TOOLSETS

    for name in ("coding", "hermes-cli", "hermes-cron", "hermes-acp", "hermes-api-server"):
        assert "herdr" in TOOLSETS[name]["includes"], name


def test_check_fn_is_false_without_herdr_or_ssh(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "local", "transport": "local"}]))
    with patch("shutil.which", return_value=None):
        assert herdr_tools.check_herdr_available() is False


def test_check_fn_is_true_with_remote_server_and_ssh(monkeypatch):
    monkeypatch.setenv("HERDR_SERVERS", json.dumps([{"name": "k", "transport": "ssh", "target": "k"}]))
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/ssh" if name == "ssh" else None):
        assert herdr_tools.check_herdr_available() is True
