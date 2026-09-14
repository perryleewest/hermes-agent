# Handoff: tasks that need a session with machine access

**Written** 2026-09-14 by a Claude Code session running in Anthropic's cloud.
**Revised** 2026-09-14, same session, after a full sweep of the repositories it
could reach. See "Open pull requests" and Task 6 below for what that added.
**For** a Claude Code session running on one of Perry's own machines.

That cloud session could reach GitHub but had **no network route to khadas,
mac-mini, hetzner or beelink**, and could not message sessions running on them.
Everything below is work it identified and could not perform. Each task states
what to do, how to verify it, and what will go wrong if you do it carelessly.

Perry is not a software developer. Report in plain language: does it work, what
does he need to do, did anything break. Skip the process detail.

---

## Context you need first

**How agent context files load.** Hermes loads the FIRST of
`.hermes.md` / `HERMES.md` -> `AGENTS.md` -> `CLAUDE.md` and **ignores the
rest**. Only `HERMES.md` walks up to the git root; the others are read from the
current directory only. Claude Code reads `CLAUDE.md`.

So a repo with both `AGENTS.md` and `CLAUDE.md` gives the two tools **different
instructions**, silently. Two repos were already found broken this way and
fixed (`mesh-monorepo`, `big-brain` - in the latter, a "secrets in .env, do NOT
merge" warning was invisible to Hermes). Do not add a `HERMES.md` to a repo
that already has a working context file: it outranks them and leaves the
others as dead weight.

**The machines.** hetzner (Ubuntu VPS), beelink (Ubuntu), mac-mini (macOS
arm64), khadas (**Windows 11** - confirmed by Perry 2026-09-14). khadas being
Windows is load-bearing: it is why Hrdle cannot run there, why
`herdr machine add` cannot use it, and why several "linux and macOS only" tools
exclude it.

---

## Task 1 - Test the khadas SSH pane  (HIGHEST VALUE, do this first)

Perry's agent control surface is **one Herdr server on the mac-mini**, fronted
by **Hrdle** (github.com/hrdle/hrdle) on his EVEN Realities G2 glasses. Hrdle is
a front-end over the local Herdr server - "Hrdle runs every session as a herdr
workspace" - so the glasses already show that Herdr.

The plan is to put the other machines on that same surface by opening panes in
the mac-mini's Herdr that SSH out. Everything about it is verified except one
thing: whether a full-screen agent UI renders correctly over **Windows** SSH
(ConPTY can garble redraws and mishandle keys).

**On the mac-mini**, in a Herdr pane:

```bash
HERDR_AGENT=hermes ssh khadas -t hermes
```

`HERDR_AGENT=<kind>` is required and documented: Herdr sees the pane's
foreground process as `ssh`, not the agent, and this override tells it which
screen manifest to use. Valid kinds include `hermes`, `claude`, `codex`,
`cursor`, `opencode`, `grok`, `gemini`, `droid`, `copilot`, `kimi`, `qwen`.

**Verify:** the agent's UI renders legibly, keys work, and the pane shows a
working/blocked/idle indicator in Herdr. Then check the glasses show that
session with its indicator.

**Then repeat for the Linux hosts**, which should be clean:

```bash
HERDR_AGENT=claude ssh hetzner -t claude
HERDR_AGENT=claude ssh beelink -t claude
```

**Known limitation, do not treat as a bug:** SSH panes will NOT auto-resume
after a Herdr restart. Herdr's integration reports session identity through
`HERDR_ENV`, `HERDR_PANE_ID`, `HERDR_BIN_PATH` and `HERDR_SOCKET_PATH` -
environment variables plus a local socket, none of which cross SSH. Live status
works; restore does not.

**If khadas renders badly:** the fix is running khadas's agents under **WSL2**
rather than native Windows. WSL2 presents as `linux-x64`, which would also make
khadas eligible to run Hrdle itself and to be a valid SSH target for
`herdr machine add` and `herdr-mirror`. That is a real service migration and is
covered by the vault's no-new-services rule - **get Perry's sign-off first.**

---

## Task 2 - Install the fixed stop hook

`~/.claude/stop-hook-git-check.sh` currently reports work as unsaved when it
is not. It did this twice in one session, and acting on either would have
caused damage:

1. A vault clone made on Linux showed 6 modified files - 14,338 insertions and
   14,338 deletions, and **zero real content change**. Pure CRLF->LF
   normalisation. Pushing it would have recreated a known incident: the vault's
   own `.gitattributes` records an earlier case where a 3,350-line change
   reported 39,608 insertions and 36,400 deletions and "looked exactly like
   mass data loss" during an audit.
2. A branch whose commit was already on the remote **and already merged into
   main** was reported as unpushed, because a `git fetch origin main` had not
   refreshed that branch's remote-tracking ref.

A corrected script is in this repo at **`docs/handoff/stop-hook-git-check.sh`**.
It adds two checks: `git diff --ignore-cr-at-eol` before calling a tree dirty,
and `git merge-base --is-ancestor` before calling a commit unpushed.

**Diff it against the existing one before replacing** - it was written from
observed behaviour, not from Perry's source, so his may do more.

It was tested against five cases: line-endings-only (silent), a real edit
(warns), an untracked file (warns), an already-merged commit with a lost
tracking ref (silent), a genuinely unpushed commit (warns).

---

## Task 3 - Rename the dot-prefixed repositories

At least seven of Perry's GitHub repos begin with `.`:

```
.semantica-er   .entity-resolve   .tbd-classify   .planning
.sisyphus       .tribe-mesh-config   .email-send-gate
```

**Every one of them is unreachable from a Claude Code Remote session:**

> `add_repo: repository name ".semantica-er" begins with '.', so its clone
> directory would be a hidden path under this session's working directory and
> could collide with configuration directories (e.g. ~/.claude). Repositories
> whose names begin with '.' cannot be attached to this session.`

The GitHub API refuses them too, for scope reasons. This is why the cloud
session could not see the Hermes-semantica work Perry asked about - work he
says Codex and Crush agents have done in `.semantica-er`.

**Fix:** rename each to drop the leading dot (`.semantica-er` ->
`semantica-er`). GitHub redirects the old name, so existing clones and remotes
keep working. Renaming is a settings operation - confirm each with Perry, and
check whether any script or config references the dotted name first:

```bash
grep -rn "\.semantica-er\|\.entity-resolve\|\.tbd-classify" ~/ --include="*.sh" --include="*.json" --include="*.yaml" --include="*.toml" 2>/dev/null
```

---

## Task 4 - The vault's Hermes master document

`mesh-brain/HERMES-SYSTEM.md` is ~16KB titled *"Hermes System Master Document -
Master system specification and context hub for all Hermes infrastructure"*.

**Hermes never loads it.** The filename must be exactly `HERMES.md` or
`.hermes.md`; `HERMES-SYSTEM.md` does not match, so it is an ordinary note.
Hermes reads the vault's `AGENTS.md` (267 lines of vault rules) instead.

The vault's `CLAUDE.md` is a deliberate 14-line stub pointing at `AGENTS.md` -
that part is correct and should stay.

**Do NOT simply rename `HERMES-SYSTEM.md` to `HERMES.md`.** It would outrank
`AGENTS.md`, and Hermes would stop seeing all 267 lines of vault rules, since
only one file ever loads. It needs a **merge**: fold the Hermes master content
into `AGENTS.md`, or build a combined `HERMES.md` that carries both.

**Timing matters.** The vault auto-commits from khadas via obsidian-git. Do
this when Obsidian is not mid-sync, and check `git status` in the vault first.
**Never clone the vault on Linux and push from it** - see Task 2, case 1.

---

## Task 5 - Herdr's Hermes integration (low priority)

On each machine that runs Hermes inside a Herdr pane:

```bash
herdr integration install hermes
herdr integration status          # expect Hermes Agent version 5
```

This writes `plugins/herdr-agent-state/` under `HERMES_HOME` and reports the
resumable session id so Herdr can restore a pane with `hermes --resume <id>`.
It is a **local** mechanism and does not apply to agents reached through an SSH
pane (Task 1), so its value is limited if the SSH-pane design is adopted.

Optional: the bundled Hermes plugin for driving Herdr over SSH is merged into
`main` of `perryleewest/hermes-agent`. It is **not** needed for the glasses
surface - one Herdr with SSH panes is simpler. It is for voice control,
unattended automation, and reaching hosts when the mac-mini is down. Install
only if Perry wants those:

```bash
hermes plugins install perryleewest/hermes-agent/plugins/herdr --enable
```

---

## Open pull requests - Perry's to review, not yours to merge

Ten draft PRs were opened from the cloud session. **Do not merge them** - they
are Perry's call. They are listed here only so you do not redo the work or get
confused by a branch that already exists.

All ten are on branch `claude/hermes-herdr-glasses-integration-73diw0` in their
own repositories, all in sync with their remotes.

Three repositories had **no context file at all**, so any agent opening them
started from the source with no orientation. Each got one written from the
repository's own contents:

| Repo | PR |
|---|---|
| `fleet-ops` | #1 |
| `hub-rs` | #1 |
| `agent-dispatch` | #12 |

Seven had a good context file under **one name only** - `CLAUDE.md`, which only
Claude Code reads. Hermes, codex, opencode and crush look for `AGENTS.md`,
found nothing, and arrived blind at repositories Claude knew well. Each got a
byte-identical `AGENTS.md`:

`context-engine` #2 - `commands` #2 - `anthropic-bridge` #3 -
`agent-telemetry` #2 - `comms-vault-sync` #2 - `claude-memory-mycelium` #2 -
`amp-spec` #4

Only two of those repositories run CI. Both are green. `agent-dispatch` was red
**before** the branch existed, on two dependency advisories in a `Cargo.lock`
identical to `main`'s: RUSTSEC-2026-0190 (unsoundness in `anyhow`'s
`Error::downcast_mut()`) and a yanked `chacha20 0.10.0` pulled in transitively
through `async-nats`. Fixed in that PR by a targeted
`cargo update -p anyhow -p chacha20` - lockfile only, no API change, full gate
run passing locally on the pinned 1.96.0 toolchain.

**If you keep these files, keep both names identical.** When either changes,
change both. That is the entire point of the pair.

---

## Task 6 - Two repository decisions only Perry can make

Neither is urgent. Both need his answer before anyone acts.

**`hermes-agent` has `AGENTS.md` but no `CLAUDE.md`.** So a Claude Code session
opening the Hermes fork gets nothing, which is the same failure as the seven
repos above, mirrored. The fix is a small pointer file. The cloud session did
**not** do it: the only branch it was allowed to push there already carried the
Herdr plugin PR, and dropping an unrelated docs file into that PR would muddy
it. If Perry wants it, it is a two-minute change on its own branch.

**`hermes-cli-agent` is an empty repository.** It has exactly one commit -
*"Initial commit: hermes-agent repo split from home monorepo"* - and that commit
contains **zero files**. The split was started and nothing was ever moved. It is
one of five empty repos on the account (`orchestrator`, `coordinator`,
`grok-agent`, `kimi-agent`, `hermes-cli-agent`). Either finish the split or
delete the repo; it does nothing as it stands.

---

## Reference

- Full architecture: `docs/herdr-glasses-control-surface.md` in this repo
- The plugin and its config block: `plugins/herdr/README.md`
- Hrdle: https://github.com/hrdle/hrdle  ("Hrdle = herdr + handle")
- Herdr: https://github.com/herdrdev/herdr

## Report back

Tell Perry, in plain language:

1. Did the khadas pane render correctly? That one answer decides whether the
   whole single-Herdr design works or whether WSL2 is needed.
2. Anything you changed on his machines.
3. Anything that broke.
4. His answer on Task 6, if he gives you one.
