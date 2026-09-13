# Herdr plugin

Lets Hermes drive [Herdr](https://github.com/herdrdev/herdr) — the terminal
runtime coding agents live in — across **every Herdr server you run, as one
merged control surface**.

Verified against herdr `0.9.0` (`38bc172`).

## Why this exists

Herdr already knows what Hermes is. Its source ships:

- `src/detect/manifests/hermes.toml` — screen rules that classify a Hermes pane
  as `working`, `blocked`, or `idle`
- `Agent::Hermes` in `src/detect/mod.rs`, with the aliases `hermes` and
  `hermes-agent`
- `herdr agent start --kind hermes`
- `herdr integration install hermes`, which writes
  `plugins/herdr-agent-state/` into `HERMES_HOME` and reports the resumable
  session id so Herdr can restore the pane with `hermes --resume <id>`

That is Herdr → Hermes. This plugin is Hermes → Herdr.

## Why it fans out itself instead of using `herdr machine add`

Herdr has its own multi-machine feature, but its documentation is explicit that
multi-machine connections are supported on Linux and macOS clients talking to
Linux and macOS servers, and that **native Windows servers are not supported as
SSH targets**. A Windows Herdr server therefore cannot join that merged window.

So the merge happens one rung up: this plugin runs the Herdr CLI against each
configured server — locally, or via `ssh <target> herdr ...` — and combines the
results. That works regardless of the remote OS, and it keeps Herdr's own
failure isolation: one unreachable machine is reported in `unreachable` while
every other machine still answers.

## Configuration

In `config.yaml`:

```yaml
herdr:
  servers:
    - name: mac-mini
      transport: ssh
      target: perry@mac-mini
    - name: khadas
      transport: ssh
      target: perry-lee@khadas
      shell_quoting: windows      # Windows OpenSSH runs commands via cmd.exe
```

Or as JSON in the `HERDR_SERVERS` environment variable. With neither set, a
single implicit `local` server is used.

| Field | Meaning |
| --- | --- |
| `name` | How you refer to the server in tool calls. |
| `transport` | `local` or `ssh`. Defaults to `ssh` when `target` is set. |
| `target` | SSH destination. A bare string entry is shorthand for this. |
| `bin` | Path to `herdr` on that host. Default `herdr`. |
| `shell_quoting` | `posix` (default) or `windows`. POSIX single quotes are literal characters to `cmd.exe`, so Windows targets need `windows`. |
| `ssh_options` | Extra `ssh -o` values, e.g. `["Port=2222"]`. |
| `env` | Extra environment for the call, e.g. `HERDR_SOCKET_PATH` for a named session. |
| `label` | Display name used by `herdr_board`. |
| `enabled` | Set `false` to keep a profile without connecting. |

Authentication stays entirely with OpenSSH — nothing here stores keys or
passwords, and SSH runs with `BatchMode=yes` so a missing key fails fast
instead of hanging on a prompt.

## Tools

| Tool | What it does |
| --- | --- |
| `herdr_servers` | `list` configured servers, or `status` to probe each. |
| `herdr_agents` | `list` every agent on every server (sorted by how much it needs you), plus `get` / `read` / `explain`. |
| `herdr_control` | `prompt`, `send_keys`, `wait`, `start`, `focus`, `rename`, `notify`. |
| `herdr_panes` | `workspaces`, `list`, `split`, `run`, `read`, `wait_output`, `close`. |
| `herdr_board` | A compact merged board sized for a small display. |

Read-only actions default to **all** servers. Mutating actions require an
explicit `server`, because Herdr scopes pane ids and agent names to one server —
two machines can both have a `w1:p1` and both have an agent named `reviewer`.

## Availability

The plugin is bundled and `kind: backend`, so it auto-loads with no
`plugins.enabled` entry (the same pattern `plugins/spotify` uses). The `herdr`
toolset is included by `coding`, `hermes-cli`, `hermes-cron`, `hermes-acp` and
`hermes-api-server`.

Every tool is gated by a `check_fn`: the tools always appear in `hermes tools`,
but only dispatch where a local `herdr` binary, or `ssh` plus a configured
remote server, actually exists. The gate does not make a network call, so
listing tools stays cheap.

## Notes on robustness

Herdr's JSON envelope differs per command and per version, so responses are
parsed tolerantly: a bare list, a `{"agents": [...]}` mapping, and a single
record are all accepted, and each agent keeps its original record under `raw`.
Agent state is read from `status`, `state`, or a nested `status.state`.

If Hermes is itself running inside a Herdr pane, `HERDR_PANE_ID` and
`HERDR_ENV` are stripped from the call environment so the pane's own socket
cannot silently retarget a call meant for another server.
