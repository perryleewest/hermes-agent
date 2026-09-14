# One control surface: every machine's agents, on the G2 glasses

How to see and drive coding agents on every machine in the fleet from a pair of
EVEN Realities G2 glasses.

Verified against herdr `0.9.0` (`38bc172`) and Hrdle `0.3.197` (`84a1dd3`).

## The design

**One Herdr server, on the mac-mini. Panes SSH out to everything else.**

```
  EVEN Realities G2 glasses
        |
  Hrdle  (github.com/hrdle/hrdle, on the mac-mini)
        |   Hrdle is a front-end over the local Herdr server:
        |   "Hrdle runs every session as a herdr workspace"
        v
  Herdr  (mac-mini -- the only Herdr server in the fleet)
        |
        +-- pane: agent running locally on the mac-mini
        +-- pane: ssh hetzner  -> agent runs there
        +-- pane: ssh beelink  -> agent runs there
        +-- pane: ssh khadas   -> agent runs there   (Windows 11)
```

Nothing needs to be installed to make this work. Hrdle already serves the
mac-mini's Herdr to the glasses; adding SSH panes to that Herdr puts every
machine on the surface the glasses are already looking at.

### Why it holds together

Hrdle is not a separate session manager that happens to bundle Herdr. From its
README: *"Hrdle runs every session as a herdr workspace"*, *"Sessions live in
the herdr server process"*, and *"Session indicators (working / waiting / done)
need no hooks at all -- herdr reports agent status itself"*. It drives Herdr's
socket API. Its panes are Herdr panes.

So anything that appears in that Herdr appears in Hrdle, and therefore on the
glasses.

A Herdr pane is an ordinary terminal, so `ssh <host>` in a pane puts the pane on
that host. The agent runs remotely; its UI streams back into the local pane
buffer.

### Making Herdr recognise a remote agent

Herdr identifies the pane's foreground process, which for an SSH pane is `ssh`,
not the agent. Herdr documents the override for exactly this shape:

> a host-visible wrapper can hide the real agent process from Herdr. Set
> `HERDR_AGENT=<agent>` on the wrapper command to tell Herdr which existing
> agent screen manifest to use.

So the pane command is:

```bash
HERDR_AGENT=hermes  ssh khadas  -t hermes
HERDR_AGENT=claude  ssh hetzner -t claude
```

Herdr then evaluates that agent's screen manifest against the buffer -- which
holds the remote agent's live UI -- and marks the pane `working`, `blocked`,
`idle` or `done`. Those are the indicators the glasses show.

`--kind` values Herdr knows include `hermes`, `claude`, `codex`, `cursor`,
`opencode`, `grok`, `gemini`, `droid`, `copilot`, `kimi`, `qwen`.

### Windows is not a problem here

Plain `ssh` **into** Windows is fine. The Windows restriction that shapes
everything else in this document -- "native Windows servers are not supported as
SSH targets" -- is about Herdr treating a host as a *Herdr server* over its own
protocol. Opening an interactive shell on one is ordinary SSH.

## What this costs

**SSH panes do not auto-resume.** Herdr's agent integration reports session
identity through `HERDR_ENV`, `HERDR_PANE_ID`, `HERDR_BIN_PATH` and
`HERDR_SOCKET_PATH` -- environment variables plus a local socket, none of which
cross SSH. Live status works; automatic restore after a Herdr restart does not.
Those panes come back as plain shells.

**The mac-mini becomes load-bearing.** It is the only Herdr server and the
glasses' only path in. If it sleeps, the whole surface is gone. It is already
the always-on glasses/voice node, so this is usually acceptable -- but it is now
a single point of failure by design, not by accident.

**Khadas is the one render risk.** Full-screen terminal UIs over Windows SSH
(ConPTY) can redraw badly or mishandle keys. macOS and Linux panes will be
clean. Test the khadas pane specifically before relying on it.

**macOS's own `ssh` may break the status indicator -- REPORTED, NOT YET
CONFIRMED.** A session testing this design against beelink on 2026-09-14
reported that the mac-mini's native `ssh` blocked the Herdr status light, and
that wrapping the call in a small Python wrapper fixed it -- along with a
`claude` path problem in the same pane. On that report the pane worked and the
indicator came back.

Recorded here because it directly contradicts the "just run `ssh`" instruction
above, and the session that found it was archived before the detail could be
pinned down. **Two things are still unknown:** whether native `ssh` produced no
indicator at all or a stuck one, and what specifically the wrapper changed --
PTY allocation, environment, or the `claude` path alone. Until someone answers
those, treat the plain `ssh` command in this document as the *intended* design
rather than a verified one, and expect to need a wrapper on macOS.

If you confirm or refute this, correct this section and say which it was.

## Setup

1. Confirm `ssh khadas`, `ssh hetzner`, `ssh beelink` all work from the mac-mini
   non-interactively (key auth, no password prompt).
2. In Hrdle, open a pane and run the `HERDR_AGENT=... ssh ...` command for one
   host. Confirm the pane gets a status indicator. **If it does not, that is the
   reported macOS `ssh` problem above, not a mistake on your part** -- try the
   Python wrapper route before assuming the design is wrong.
3. Check the glasses show that session with its indicator.
4. Repeat per host. Give the sessions colours (long-press) so they are
   distinguishable at a glance on the lenses.
5. Do khadas last, and look hard at whether the UI renders correctly.

## If khadas renders badly: WSL2

Running khadas's agents under **WSL2** rather than native Windows removes the
cause rather than routing around it. WSL2 presents as `linux-x64`, which also
makes khadas eligible to run Hrdle itself, to be a Hrdle peer over Tailscale,
and to be a valid SSH target for `herdr machine add` and `herdr-mirror`.

Every Windows constraint in this fleet traces back to native Windows. This is a
real migration of a running service, not a toggle, and the vault's
no-new-services rule applies -- operator sign-off required. Untested here.

## The Hermes route: what it is still for

`plugins/herdr` lets Hermes drive Herdr across several servers by running the
Herdr CLI over SSH per host. It was built when the fleet was assumed to need two
Herdr servers bridged, and for **the glasses surface it is now redundant** --
one Herdr with SSH panes is simpler and has no LLM in the loop.

It remains the right tool for three things:

- **Voice and natural language.** Hermes Lens -> Hermes -> Herdr answers "what's
  running?" or "tell the builder to rerun the tests" in a sentence. Hrdle is a
  direct-manipulation surface; this is a conversational one.
- **Automation.** Cron jobs and unattended work that need to inspect or steer
  agents without a human at a screen.
- **Fallback.** If the mac-mini is down, Hermes can still reach khadas and the
  Linux hosts directly, because it never depends on one Herdr being up.

Use Hrdle as the daily surface. Use the Hermes route for voice, automation, and
the day the mac-mini is off.

## Herdr already recognises Hermes

Independent of any of the above, and requiring nothing written: Herdr ships a
`hermes` detection manifest, an `Agent::Hermes` kind with `hermes` /
`hermes-agent` aliases, `agent start --kind hermes`, and
`herdr integration install hermes` for session restore.

Run on each host that runs Hermes inside Herdr:

```bash
herdr integration install hermes
herdr integration status          # expect Hermes Agent version 5
```

That writes `plugins/herdr-agent-state/` under `HERMES_HOME` and reports the
resumable session id so Herdr can restore a pane with `hermes --resume <id>`.
Note this is a *local* mechanism -- it does not apply to agents reached through
an SSH pane, per the restore limitation above.

## Record of corrections

This document was wrong three times, and the failures are recorded because the
cause was procedural and cheap to repeat.

1. It claimed the Herdr ecosystem had no smart-glasses plugin. The catalogue
   grep behind that claim was capped with `head -20`; the matching entries sit
   at lines 1513 and 1936. **A truncated search returning nothing is not
   evidence of absence.**
2. It then identified the mac-mini's glasses plugin as Hermes Lens, and later as
   `pawaca/even-better`. It is neither: it is
   [Hrdle](https://github.com/hrdle/hrdle) -- *"Hrdle = herdr + handle"*.
   [Hermes Lens](https://github.com/Pb-207/Hermes-Lens) is a different G2 plugin
   that front-ends Hermes itself; `pawaca/even-better` is a narrower
   Herdr-to-G2 mirror.
3. It treated "khadas runs Windows 11" as established while it was actually
   inherited from a notes file. It is now confirmed with the operator
   (2026-09-13), and it is the fact the whole architecture turns on.
