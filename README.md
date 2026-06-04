# BopBop

**Your machine's Claude, reachable from your pocket.**

BopBop turns the [Claude Code](https://claude.com/claude-code) install on
your computer into a personal always-on agent you can text over Signal.
Every message you send (Note to Self) spawns a Claude Code turn on your
machine — with whatever context, scripts, and tools you've given it.

```
 phone (Signal)  ──►  signal-cli-rest-api  ──►  bopbop server  ──►  claude -p
                                                      │           (your context
                                                      ▼            dir = its
                                              sqlite history       brain)
```

## The context-dir idea

BopBop itself is a thin gateway (~1.2k lines of Python). The personality
and capabilities live in a **context directory** (`~/.bopbop/context`) —
a normal Claude Code working dir with a `CLAUDE.md`, a `personality.md`,
and any scripts you add. Teaching your agent a new trick is: drop a
script in the context dir, document it in `CLAUDE.md`, done. No server
code changes.

This is the same pattern you'd use for a human teammate: don't change the
messenger, change the onboarding doc.

## Install

Prereqs: Linux with systemd (user instance), Python 3.11+, [Claude Code](https://claude.com/claude-code)
installed and authenticated, Docker (for the Signal bridge), a Signal
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
```

## Security model — read this

BopBop runs Claude with `--dangerously-skip-permissions`: the agent has
**full access to your machine** — that's the point, and the risk.

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
| `BOPBOP_CONTEXT_DIR` | `~/.bopbop/context` | the agent's brain (Claude Code working dir) |
| `BOPBOP_DATA_DIR` | `~/.bopbop/data` | sqlite db + downloaded attachments |
| `BOPBOP_CLAUDE_BIN` | `claude` | Claude Code binary |
| `BOPBOP_CLAUDE_MODEL` | `sonnet` | model per turn |
| `BOPBOP_SIGNAL_ENABLED` | — | `1` to enable the Signal channel |
| `SIGNAL_HTTP_URL` | `http://127.0.0.1:8080` | signal-cli-rest-api |
| `SIGNAL_ACCOUNT` | — | your number, E.164 |
| `SIGNAL_ALLOWED_USERS` | — | comma-separated allowlist (required) |
| `BOPBOP_REQUIRE_BEARER` | `0` | gate `/api/chat` with a bearer token |

## Niceties

- **Session warmth**: consecutive messages resume the same Claude session
  (1h idle window or 20 turns, whichever lasts longer), so "and what
  about the second one?" works. `/reset` starts fresh.
- **Attachments**: send a photo; the agent reads it with Claude's
  vision-capable Read tool.
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
