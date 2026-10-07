# BopBop

**Your machine's coding agent, reachable from your pocket.**

BopBop turns a Claude Code or Codex CLI install on your computer into a
personal always-on agent you can text over Signal. Every message you send
(Note to Self) spawns an agent turn on your machine — with whatever context,
scripts, and tools you've given it.

```
 phone (Signal)  ──►  signal-cli-rest-api  ──►  bopbop server  ──►  claude -p
                                                      │               or
                                                      ▼           codex exec
                                              sqlite history
```

## The context-dir idea

BopBop itself is a thin gateway (~1.2k lines of Python). The personality
and capabilities live in a **context directory** (`~/.bopbop/context`) —
a normal agent working dir with `AGENTS.md`/`CLAUDE.md`, a personality file,
and any scripts you add. Teaching your agent a new trick is: drop a script in
the context dir, document it in `CLAUDE.md`, done. Codex turns explicitly load
that shared context in addition to native `AGENTS.md`.

This is the same pattern you'd use for a human teammate: don't change the
messenger, change the onboarding doc.

## Install

Prereqs: Linux with systemd (user instance), Python 3.11+, Claude Code or Codex
CLI installed and authenticated, Docker (for the Signal bridge), and a Signal
account on your phone.

```bash
git clone https://github.com/wabbazzar/bopbop && cd bopbop
./install.sh
```

The installer is idempotent. It scaffolds `~/.bopbop/`, creates the
server venv (the clone must stay where you installed from — the systemd
unit points into it), starts a `signal-cli-rest-api` container, installs
`bopbop.service` (systemd user unit), and runs `bopbop doctor`.

One-time Signal link:

1. open `http://127.0.0.1:8080/v1/qrcodelink?device_name=bopbop`
2. phone → Signal → Settings → Linked Devices → Link New Device → scan
3. edit `~/.bopbop/env`: set `SIGNAL_ACCOUNT` and `SIGNAL_ALLOWED_USERS`
   to your number
4. `systemctl --user restart bopbop && bin/bopbop doctor`

Then text yourself in Signal (Note to Self). That's your agent.

## CLI

```
bin/bopbop init      scaffold ~/.bopbop (context dir, data dir, env file)
bin/bopbop doctor    end-to-end health check
bin/bopbop run       run the server in the foreground (dev)
bin/bopbop pack      install / list / remove context packs
```

### Experimental local terminal agent

`python3 bin/bopbop-local.py` opens a direct Ollama agent in the current
directory using `gpt-oss:20b`. It does not use Claude or Codex and does not
change the Signal service. It shows tool calls, tool results, per-call token
counts, model throughput, and response times. Pass a prompt as arguments for
one turn, or use `--workspace PATH` to choose another working directory.
`run_command` runs with your user permissions; use a workspace you trust.

## Context packs

A **pack** is an installable capability bundle for your agent — a git repo
(or local dir) shaped like:

```
pack.toml             # name, description, fragment, install_hook, required_env
CLAUDE.fragment.md    # spliced into your context CLAUDE.md (managed markers)
scripts/ …            # whatever the fragment documents
```

```bash
bin/bopbop pack install https://github.com/someone/some-pack
bin/bopbop pack list
bin/bopbop pack remove some-pack
```

Install clones the pack into `context/packs/<name>` and splices its
fragment into your context `CLAUDE.md` between `<!-- bopbop-pack:NAME -->`
markers — so every turn knows the capability exists. Reinstall is
idempotent; remove strips the fragment again.

`pack.toml` keys (flat `key = "value"` only):

| key | meaning |
|---|---|
| `name` | kebab-case pack name (required) |
| `description` | one-liner shown in `pack list` |
| `fragment` | path to the CLAUDE.md fragment to splice |
| `install_hook` | script for system-level setup (timers, deps) |
| `required_env` | comma-separated vars the pack needs in `~/.bopbop/env` |

**Hooks never run automatically.** A pack's `install_hook` is arbitrary
code; `pack install` prints it and tells you how to run it — or pass
`--run-hook` if you've read it and trust it. `bopbop doctor` checks every
installed pack's `required_env`. Packs may live at the repo root or in a
`pack/` subdir, so a project can ship its pack alongside its main code.

## Security model — read this

BopBop runs the selected harness without interactive approvals (Claude:
`--dangerously-skip-permissions`; Codex:
`--dangerously-bypass-approvals-and-sandbox`). The agent has **full access to
your machine** — that's the point, and the risk.

- **Sender allowlist is mandatory.** The Signal channel refuses to start
  without `SIGNAL_ALLOWED_USERS`; inbound messages from senders not on it
  are dropped before Claude ever sees them. (Your own Note-to-Self always
  reaches the agent — those messages are you.)
- **The API binds 127.0.0.1 only.** If you proxy it (e.g. Tailscale
  Serve), your VPN is the trust boundary. Exposing it wider requires
  `BOPBOP_REQUIRE_BEARER=1` + a token — and exposing it to the public
  internet is a terrible idea regardless.
- **Prompt-injection detectors** scan inbound messages (jailbreak
  phrases, role-token stuffing, encoded blobs) and outbound replies
  (credential shapes); hits are logged by detector name only — message
  bodies are never logged.
- **Note-to-Self is private but your agent is not a vault**: anything it
  writes to disk or memory is as durable as your machine. Don't text it
  secrets you wouldn't put in a file.
- **This repo is public and must stay generic.** A pre-commit hook +
  CI (`scripts/leak-check.sh`) reject real phone numbers, home paths,
  personal emails, tailnet hostnames, and credential shapes. Machine-
  specific values belong in `~/.bopbop/env`, never in the repo.

## Configuration

Everything is env vars in `~/.bopbop/env` (template: `templates/env.example`):

| var | default | meaning |
|---|---|---|
| `BOPBOP_CONTEXT_DIR` | `~/.bopbop/context` | the agent's working directory and shared context |
| `BOPBOP_DATA_DIR` | `~/.bopbop/data` | sqlite db + downloaded attachments |
| `BOPBOP_AGENT_HARNESS` | `claude` | agent CLI: `claude` or `codex` |
| `BOPBOP_CLAUDE_BIN` | `claude` | Claude Code binary |
| `BOPBOP_CLAUDE_MODEL` | `sonnet` | model per turn |
| `BOPBOP_CODEX_BIN` | `codex` | Codex CLI binary |
| `BOPBOP_CODEX_MODEL` | unset | optional Codex model override; unset uses Codex config |
| `BOPBOP_SIGNAL_ENABLED` | — | `1` to enable the Signal channel |
| `SIGNAL_HTTP_URL` | `http://127.0.0.1:8080` | signal-cli-rest-api |
| `SIGNAL_ACCOUNT` | — | your number, E.164 |
| `SIGNAL_ALLOWED_USERS` | — | comma-separated allowlist (required) |
| `BOPBOP_REQUIRE_BEARER` | `0` | gate `/api/chat` with a bearer token |

## Niceties

- **Session warmth**: consecutive messages resume the same harness-specific
  session (1h idle window or 20 turns, whichever lasts longer), so "and what
  about the second one?" works. Switching harnesses starts fresh; `/reset`
  also starts fresh.
- **Attachments**: send a photo; the local attachment path is included for the
  selected agent to inspect.
- **Typing indicator** while the agent works; long replies are split at
  paragraph boundaries.
- **Test without a phone**: `curl -X POST http://127.0.0.1:8090/api/test/signal-inject -H 'Content-Type: application/json' -d '{"message":"hi"}'`
  (localhost-only) pushes a message through the full production path.
- **HTTP API**: `POST /api/chat {"message": "..."}` streams NDJSON —
  build any frontend you like on it.

## Status / roadmap

Extracted from a personal setup that's been running for a while; the
public packaging is young. Linux/systemd only for now. Roadmap: macOS
(launchd), more channels, a reference PWA frontend.

## License

MIT
