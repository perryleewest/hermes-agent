---
name: herdr
description: "Run and supervise coding agents inside Herdr across every machine at once: one merged agent list, answer blocked approval prompts, start and steer agents."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [herdr, orchestration, agents, multi-machine, terminal, glasses]
    category: software-development
    related_skills: [plan, requesting-code-review]
---

# Herdr

[Herdr](https://github.com/herdrdev/herdr) is the terminal runtime coding agents
live in. Every pane it manages is marked `working`, `blocked`, `idle`, `done`,
or `unknown`. The `herdr_*` tools let you see and steer those panes on **every
configured Herdr server at once** — that merged view is the whole point, because
the user may be asking from a phone or a pair of smart glasses where they cannot
see any terminal.

## Start here

`herdr_agents(action="list")` queries every server and returns agents sorted by
how much they need a human: `blocked` first, then `done`, `working`, `idle`.
Each record is tagged with the `server` it came from.

For a short answer — speaking aloud, or rendering to a small display — use
`herdr_board()`. It returns a `headline`, fixed-width `lines`, and a
`display_text` already sized for a heads-up display.

Lead with what is blocked. "Two agents need you: reviewer on mac-mini is waiting
on a permission prompt, builder on khadas finished" is the useful sentence.
Never read out a full table when one line answers the question.

## The state vocabulary

| State | Means | What to do |
| --- | --- | --- |
| `blocked` | Herdr matched a visible approval, question, or permission dialog. | Read it, then answer with `send_keys`. |
| `done` | Finished and not yet looked at. | Summarise the result. |
| `working` | Mid-turn. | Leave it alone, or `wait`. |
| `idle` | Ready for input. | Safe to `prompt`. |
| `unknown` | An agent is present but Herdr cannot classify it. | **Not** proof of success — read the pane before claiming anything. |

## Answering a blocked agent

Never guess at a dialog. Read it first, then press keys deliberately:

```
herdr_agents(action="read", server="mac-mini", target="reviewer",
             source="recent-unwrapped", lines=40)
herdr_control(action="send_keys", server="mac-mini", target="reviewer",
              keys=["enter"])
```

`herdr_control(action="prompt", ...)` refuses a blocked agent with
`agent_blocked` rather than typing into a dialog — that is deliberate. Use
`send_keys` for `enter`, `esc`, `down`, `ctrl+c`.

**Approvals are the user's call.** Read the dialog back to them and let them
decide unless they have already told you how to answer this class of prompt.
Approving a destructive command on their behalf is not yours to do.

## Prompting and waiting

```
herdr_control(action="prompt", server="khadas", target="builder",
              text="rerun the failing test", wait=True,
              until=["idle", "blocked"], timeout_ms=120000)
```

`until` requires `wait=True`. A timeout does **not** prove the prompt was never
delivered — read the agent before sending it again, or you will submit the same
work twice.

## Starting an agent

`agent start` needs an **existing shell pane sitting at its prompt**; it never
creates layout. Split first, then start:

```
herdr_panes(action="split", server="mac-mini", pane="w1:p1", direction="right")
herdr_control(action="start", server="mac-mini", name="reviewer",
              kind="claude", pane="w1:p2")
```

Kinds include `hermes`, `claude`, `codex`, `cursor`, `opencode`, `grok`,
`gemini`, `droid`, `copilot`, `kimi`, `qwen`, and others. Names must match
`[a-z][a-z0-9_-]{0,31}` and be unique among live agents.

## Servers are separate namespaces

Pane ids and agent names are scoped to one Herdr server: two machines can both
have `w1:p1` and both have an agent named `reviewer`. Read-only calls fan out to
every server; anything that mutates requires an explicit `server`, and the tool
will refuse with a list of configured names rather than guess. When the user
says "the reviewer" and two machines have one, ask which.

Use `herdr_servers(action="status")` when something looks wrong. A server that
is down reports under `unreachable` while the others still answer — say which
machine is unreachable rather than reporting "no agents".

## Do not

- Do not claim work succeeded because state is `unknown` or because a wait
  returned. Read the pane.
- Do not `prompt` an agent that is `working` unless the user asked you to
  interrupt it.
- Do not run `herdr` through the `terminal` tool. These tools already handle
  server selection, remote transport, and JSON parsing; a raw `herdr` call from
  inside a Hermes pane silently targets that pane's own server.
