# Herdr + Hermes + G2 glasses: one control surface for every machine

How to run Hermes and [Herdr](https://github.com/herdrdev/herdr) together so that
two Herdr servers — one on **khadas** (Windows 11), one on **mac-mini** (macOS) —
appear as a single surface you can drive by voice from Even Realities G2 glasses.

Verified against herdr `0.9.0` (`38bc172`) and
[Hermes Lens](https://github.com/Pb-207/Hermes-Lens) `0.4.24` (`70a1245`).

## The shape of it

```
  Even Realities G2 glasses
        |  temple press -> voice
        v
  Hermes Lens  (Even Hub plugin; phone page holds config)
        |  POST /api/sessions/{id}/chat/stream
        v
  Hermes gateway            <-- one brain, one session history
        |  plugins/herdr
        +----------------> herdr @ khadas    (Windows 11)
        +----------------> herdr @ mac-mini  (macOS)
```

The glasses never talk to Herdr. They talk to Hermes, and Hermes reaches both
Herdr servers. That is what makes one control surface: there is nothing to
merge on the glasses, because the merge already happened in Hermes.

## Part 1 — Herdr recognising Hermes

This direction is already built into Herdr upstream; there is nothing to write.
Herdr `0.9.0` ships:

- `src/detect/manifests/hermes.toml` — screen rules that read a Hermes pane's
  OSC title (`⏳` working, `✓` idle, `⚠` blocked) and its approval,
  clarification, and credential prompts
- `Agent::Hermes` in `src/detect/mod.rs`, aliases `hermes` and `hermes-agent`
- `herdr agent start --kind hermes`
- per-agent sound settings and session restore keyed on `herdr:hermes`

What you still run, **on each machine that hosts a Herdr server**:

```bash
herdr integration install hermes
herdr integration status          # expect Hermes Agent version 5
```

That writes `plugins/herdr-agent-state/` under `HERMES_HOME` (`~/.hermes`, or
`%LOCALAPPDATA%\hermes` on Windows) and enables `herdr-agent-state` in that
Hermes's `config.yaml`. The Hermes config directory must already exist, and
Hermes needs a restart afterwards so the plugin loads.

Its job is session identity, not state: it reports the resumable session id so
Herdr can bring a pane back with `hermes --resume <id>` after a server restart.
State keeps coming from the screen manifest above.

If a Hermes pane ever shows the wrong state, ask Herdr why rather than guessing:

```bash
herdr agent explain <target> --verbose
```

and if your Hermes build's prompts have drifted from the bundled rules, drop a
local override at `~/.config/herdr/agent-detection/hermes.toml` — local
overrides always win — then `herdr server reload-agent-manifests`.

## Part 2 — Hermes driving Herdr

That is this repo's `plugins/herdr`. It is bundled and auto-loading, and the
`herdr` toolset is carried by `coding`, `hermes-cli`, `hermes-cron`,
`hermes-acp`, and `hermes-api-server` — the last one matters here, because the
API server is the path the glasses come in on.

Add both servers to the `config.yaml` of whichever Hermes the glasses talk to:

```yaml
herdr:
  servers:
    - name: mac-mini
      label: mac
      transport: ssh
      target: perry@mac-mini
    - name: khadas
      label: khadas
      transport: ssh
      target: perry-lee@khadas
      shell_quoting: windows
```

Use `transport: local` for whichever machine that Hermes runs on, if it is one
of them. Requirements:

- SSH from the Hermes host to each Herdr host, working non-interactively
  (`BatchMode=yes` — load a passphrase-protected key with `ssh-add` first).
- `herdr` on `PATH` on each target, or set `bin:` to its full path.
- `shell_quoting: windows` on Windows targets. Windows OpenSSH runs the command
  through `cmd.exe`, where POSIX single quotes are literal characters, so a
  prompt containing spaces would otherwise arrive mangled.

Check it end to end:

```
herdr_servers(action="status")
herdr_agents(action="list")
```

### Why not `herdr machine add`

Herdr has its own multi-machine view, and it would be the obvious answer. It
does not fit here: Herdr documents multi-machine connections as Linux and macOS
clients talking to Linux and macOS servers, and states that **native Windows
servers are not supported as SSH targets**. khadas runs Windows 11, so it cannot
join that merged window.

`herdr machine add mac-mini` is still worth doing on the Mac for the desktop TUI.
It just cannot be the thing that unifies khadas with it — which is why the
merge lives in Hermes, where the transport is only "run this command over SSH"
and the remote OS stops mattering.

## Part 3 — The glasses

[Hermes Lens](https://github.com/Pb-207/Hermes-Lens) is an Even Hub plugin
(`com.pb208.evenhermes`) for the G2: the glasses are microphone and display, the
phone page holds configuration, and the backend is your own Hermes
(`/api/sessions/{id}/chat/stream`), so a session continued from the glasses is
the same session as on the desktop.

It is already what is on the Mac mini — the "HERDL"-sounding thing. It needs no
modification to become a Herdr control surface. Once Part 2 is in place, the
Hermes it points at can reach both Herdr servers, so the glasses can too.

Two settings on the phone page make it good rather than merely possible:

- **`hermes.instructions`** — the per-request instruction string. Put the
  standing orders there, e.g.

  > When asked about agents, machines, or what is running, call `herdr_board`
  > and answer in one sentence, leading with anything blocked. Never read a
  > full table aloud. Never approve a Herdr permission prompt without asking.

- **`session.labels`** — add a dedicated session (e.g. `herdr`) so the board is
  one temple press away instead of buried in a general chat.

Then the phrasings that work:

| You say | What Hermes does |
| --- | --- |
| "What's running?" | `herdr_board()` — merged, blocked first |
| "Anything need me?" | `herdr_board(attention_only=True)` |
| "What's reviewer waiting on?" | `herdr_agents(action="read", ...)` |
| "Tell builder to rerun the tests" | `herdr_control(action="prompt", ...)` |
| "Is khadas up?" | `herdr_servers(action="status")` |

`skills/software-development/herdr/SKILL.md` teaches Hermes the state
vocabulary, the blocked-agent protocol, and to keep spoken answers to one line.

### Security

Hermes Lens's own guidance applies and is worth repeating: do not expose the
Hermes gateway directly to the internet — reach it over a tunnel or your own
network. The Herdr side inherits that posture; this plugin stores no keys and
leaves authentication entirely to OpenSSH.

Note what the chain implies: anything that can talk to that Hermes can now drive
real terminals on both machines. Keep the gateway's API key set and the tunnel
private.

## What was verified, and what was not

Verified here: Herdr's Hermes support was read from herdr `0.9.0` source rather
than inferred; the plugin's configuration, command construction (including
Windows quoting), response parsing, cross-server merging, and board rendering
are covered by `tests/plugins/test_herdr_plugin.py` (39 tests).

**Not** verified: nothing in this change was run against the live khadas or
mac-mini Herdr servers — that session had no route to them. Herdr's JSON
envelope also differs per command and version, so agent-record parsing is
deliberately tolerant (bare list, `{"agents": [...]}`, or a single record; state
from `status`, `state`, or `status.state`), and every agent keeps its original
record under `raw`. If a field lands somewhere unexpected on your build,
`raw` will show it.

## Checklist

1. On khadas and mac-mini: `herdr integration install hermes`, restart Hermes,
   confirm `herdr integration status` shows Hermes Agent version 5.
2. Confirm non-interactive SSH from the Hermes host to both machines.
3. Add the `herdr.servers` block above to that Hermes's `config.yaml`.
4. `herdr_servers(action="status")` — both reachable.
5. `herdr_agents(action="list")` — agents from both machines, in one list.
6. On the phone: point Hermes Lens at that gateway, set `instructions`, add a
   `herdr` session label.
7. Temple press, "what's running?"
