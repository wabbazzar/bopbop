# BopBop

**Your machine's coding agent, reachable from your pocket.**

BopBop turns a Claude Code, Codex CLI, or local Ollama install on your computer into a
personal always-on agent you can text over Signal. Every message you send
(Note to Self) spawns an agent turn on your machine — with whatever context,
scripts, and tools you've given it.

```
 phone (Signal)  ──►  signal-cli-rest-api  ──►  bopbop server  ──►  Claude / Codex / Ollama
                                                      │
                                                      ▼
                                              sqlite history
```

## The context-dir idea

BopBop keeps its agent, interfaces, and user configuration separate. Personality
and installed capabilities live in a **context directory** (`~/.bopbop/context`)
with `AGENTS.md`/`CLAUDE.md`, a personality file, and optional context packs.
Claude and Codex can use documented scripts there. The local Ollama agent gets
callable tools from validated pack declarations; adding a script alone does not
expose it as a tool. Codex turns explicitly load the shared context in addition
to native `AGENTS.md`.

This is the same pattern you'd use for a human teammate: don't change the
messenger, change the onboarding doc.

For the code map and the rules for keeping the harness small, see
[Architecture and maintenance](docs/architecture.md). The original local
harness comparison is preserved under [research](docs/research/local-harness/report.md).

## Install

Prereqs: Linux with systemd (user instance), Python 3.11+, Claude Code or Codex
CLI installed and authenticated, or Ollama with `gpt-oss:20b` installed; Docker (for the Signal bridge), and a Signal
account on your phone.

```bash
git clone https://github.com/wabbazzar/bopbop && cd bopbop
./install.sh
# Choose the local backend during installation instead:
ollama pull gpt-oss:20b
./install.sh --harness ollama
# Or select the wrapper explicitly:
./install.sh --harness claude  # or codex
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
bin/bopbop pack      install / list / remove / tools / call / mcp
bin/bopbop            local Ollama terminal agent (current directory)
bin/bopbop local      local Ollama terminal agent, accepts --model/--workspace
bin/bopbop agent      configured interactive harness
bin/bopbop agent claude|codex|ollama   choose interactive harness explicitly
bin/bopbop web build|dev|test   optional PWA build, local development, browser gates
```

The shell alias `bopbop` points to `bin/bopbop`, so these commands work from
any directory after opening a new shell or running `source ~/.bashrc`.
`bopbop run` and the Signal service default to `BOPBOP_AGENT_HARNESS` in
`~/.bopbop/env`. The browser app's **Settings** can override the server harness,
model, context pack, and local context size without a restart. Saved preferences
in `~/.bopbop/data/agent-settings.json` take precedence over those env defaults;
remove that file to return to env configuration. `bopbop doctor` checks the
effective selection. Terminal `bopbop local` / `bopbop agent` continue to use
their CLI arguments and env settings. Changing the server harness, model, pack,
or context size starts a fresh agent session for each conversation.

### Local Ollama agent

The local agent talks directly to Ollama and is also the `ollama` server harness.
It needs Python 3.11+, a running
Ollama server, and a locally installed model with tool support. From the repo
root:

```bash
ollama pull gpt-oss:20b                 # one-time model download
bopbop                                  # interactive; workspace is current dir
bopbop local --workspace ~/code/my-project
bopbop local "summarize this repo"      # one turn
```

The default model is `gpt-oss:20b` with a 16,384-token request context. Use
`--model NAME` or `--context N` to change those at launch. The agent loads
top-level `AGENTS.md`, `CLAUDE.md`, and `personality.md` from its workspace at
startup. For a workspace with large wrapper instructions, create a concise
`BOPBOP.md`: the local agent loads that file instead. Keep required rules there
and point to longer documents to read when needed. Workspace skills under
`.agents/skills/` (or `.claude/skills/`) are listed by path; the agent reads the
requested `SKILL.md` with `read_file` before using it. Pack profiles continue
to use their own instructions and tools. Terminal conversations live in the process; Signal/API conversations
persist under `BOPBOP_DATA_DIR/ollama_sessions/` and resume through the same
server session routing as the wrapper harnesses.

#### Slash commands

Install the optional managed terminal with `bopbop ui install`. Subsequent
`bopbop` launches use an anchored input with upward menus. `--ui single` selects
it explicitly; `--ui plain` keeps the classic terminal. Without the optional
dependency, `--ui auto` (the default) uses the classic terminal. The installer
creates a repo-local `.venv`; the agent engine still needs only Python's standard
library. Server and wrapper harness dependencies are unchanged.

The managed terminal keeps conversation in its own screen: **F6** cycles focus,
arrow/Page keys scroll, **Ctrl-R** searches backward, and **Esc** returns to input.
Drag across conversation or inspector text to select and copy it to the system
clipboard. With a reader focused, **y** copies the selection or current line.
This also works in tmux with mouse mode enabled; **Shift-drag** remains the
terminal's own selection gesture.
Your terminal's native scrollback contains the startup banner, not this live
conversation. Model/tools run away from the renderer; you can edit the next
draft while they work. **Ctrl-C** stops the active turn; completed tool actions
are not undone. Completed tool groups are checkpointed; a cancelled partial
group is not added to conversation, and interrupted results are labeled in the
inspector. **Ctrl-D** on an empty input exits. The transcript retains a
bounded display tail in memory and marks evictions; nothing is saved to disk.

In an interactive terminal, typing `/` opens suggestions immediately. Type to
filter, use ↑/↓ to select, Enter to run, Tab to complete, or Esc to dismiss the
menu. Help and navigation run locally; `/call` invokes the model only when a
`:: question` is supplied:

| Command | Action |
|---|---|
| `/help` | List commands |
| `/status` | Show model, workspace, context, conversation length, and Ollama load state |
| `/stats` | Show last-turn and current-session time, tool calls, and tokens |
| `/tools` | Browse enabled tool references with arrow keys; `/tools NAME` shows one directly |
| `/call TOOL {JSON} [:: question]` | Execute a tool directly; optionally give its result to the model with a question |
| `/editor [PATH]` | Browse workspace files above the input and open one in Neovim |
| `/reset` | Clear conversation and stats, retaining workspace instructions |
| `/exit` | Leave the agent |

Piped input and one-turn prompts still accept slash commands, without the
interactive suggestion menu.

Outside a suggestion menu, **↑/↓** recalls submitted chat and command lines.
Moving back down restores your unfinished draft and cursor. History retains up
to 500 lines for the current CLI session only; recalled lines remain editable
until you press Enter. Dismiss a suggestion menu with **Esc** to recall history.

### Choose the evidence yourself

You can bypass model tool discovery when you know what to query:

```text
/call ice_query {"report":"health"}
/call ice_query {"report":"visits","window":"7d"} :: Summarize public usage and its limits
```

Without `::`, a call prints its full result and uses no model tokens. Its result
stays in the conversation for your next question; make several calls before
asking for a combined summary. With `::`, BopBop calls the tool first and sends
the evidence with your question to the model. Additional model-selected calls
remain possible. `/reset` clears the evidence, and normal history limits apply.
Arguments are validated before execution, and only enabled tools can be called.
Workspace writes and commands execute immediately, just as model-selected tools do.

`/tools` lists pack ownership and opens a reference browser in an interactive
terminal: **↑/↓** select a tool, **←/→** scroll its documentation, **Enter** keeps
the reference in your terminal scrollback, and **Esc** closes it. `/tools ice_query`
shows the same reference directly. Each panel includes typed parameters, allowed
values and bounds, examples, return-value notes, and the exact model schema.
Browsing never calls a tool or model. Type `/call ` or `/tools ` for tool-name
suggestions; selecting a `/call` suggestion inserts editable arguments and
requires another Enter to execute. These commands belong to the local terminal
harness; browser/Signal chat and wrapper harnesses keep their existing interfaces.

Once you type or select a complete tool name after `/call`, live help shows its
description, typed parameters, constraints and an example beside your draft.
It stays available while you edit arguments or a `::` question. **F1** toggles
the full reference, **Page Up/Down** scrolls help, and **Esc** hides it; **F1**
restores it. These keys preserve the draft and cursor. Long commands scroll
horizontally to fit the terminal. All references come from tool declarations;
help never executes a tool or contacts a model.

After `{`, help follows the parameter at your cursor: `{"rep` narrows the
parameter list to `report`; `{"report":"he` shows its type, description,
matching allowed values (such as `"health"`), constraints and matching examples.
After a comma, it lists parameters you have not supplied yet. Moving the cursor
back into an earlier argument shows that argument's details again. Whitespace
between the tool name and `{` is optional. F1 still opens the full reference.

The [co-driving tickets](docs/tickets/README.md) track live help, the inspector,
editor integration and completion-model research, with a user test ride between
deliveries. Automatic suggestions while typing are a later research decision.

#### Inspecting evidence and context

Run `bopbop local --ui split` to open the inspector immediately, or press **F9**
in the managed terminal. Below 100 columns, it occupies the conversation area;
input stays fixed and F9 returns to the conversation.

| Control | Action |
|---|---|
| F2 / F3 / F4 / F5 | Tool docs / returned result / exact model request / file or diff |
| F6 | Cycle input, inspector and conversation focus |
| F7 | Pin/unpin the current snapshot; new events do not replace a pinned view |
| Alt-Left / Alt-Right | Previous/next retained snapshot in this view |
| Alt-`-` / Alt-`+` | Narrow/widen the inspector |
| Ctrl-R, arrow/Page keys | Search and scroll the focused reader independently |
| Ctrl-D / Ctrl-U; Ctrl-F / Ctrl-B | Scroll the focused reader down/up half a page; a full page |
| `h j k l`, `gg` / `G`, `0` / `$` | Reader cursor movement, start/end of document, start/end of line |
| Ctrl-Space, then arrows; F8 | Select body lines and attach them to the next question |
| Mouse drag; `y` | Copy selected reader text or the current line to the system clipboard |
| Esc | Return to input and close live help |

Press F6 to focus the inspector or conversation before using reader motions;
the footer identifies the focused reader. These are read-only Vim-style
navigation bindings. For real editing, use the Neovim connection below. In the chat input,
letters remain ordinary text, Ctrl-U clears before the cursor, and Ctrl-D on
empty input exits as before.

```text
/inspect docs ice_query
/inspect result
/inspect context
/inspect file "src/example.py"
/inspect diff "src/example.py"
/attach 2:8
/detach
```

`/tools NAME` also opens that tool's docs. File paths stay inside the workspace;
diffs compare a tracked file's working contents with its HEAD version. Each
snapshot identifies its capture time, source, byte size and SHA256. Reopen a
file to refresh it. Opening a file or viewing a snapshot sends nothing to the
model. `/attach START:END` refers to the **numbered body lines**, or F8 uses the
selection/current body line in the inspector. The transcript confirms the
source, range and bytes; the footer shows pending attachment totals. These
selections accompany the next ordinary question or `/call … :: question`.
A standalone `/call` leaves pending selections intact. `/detach` and `/reset`
clear pending selections. Attachments are limited to eight selections / 32 KB.

Results contain the complete string returned by the tool dispatcher, with call
ID, arguments, duration, success/failure and source telemetry. API truncation
and stale evidence remain visible; the UI cannot recover source-omitted rows.
Context shows the exact outbound JSON after pruning, including instructions,
messages, tools and settings. Its metadata below the numbered body records
removed messages and measured model usage. HTTP headers are not captured.
Thinking-character/chunk counts describe exposed stream activity, not hidden
model reasoning or estimated token counts.

Snapshots are held only in memory: up to 32 items / 8 MiB, preserving a pin and
reporting evictions. Oversized snapshots are rejected with a notice rather than
presented as complete. File reads are limited to 256 KB. `/reset` clears model
conversation and pending attachments; retained inspector snapshots remain
available until eviction or CLI exit. No automatic snapshot export occurs.

#### Co-driving in Neovim

Run `/editor src/example.py` in the managed terminal. In tmux it opens an
adjacent **real Neovim** pane; outside tmux it opens Neovim in the same terminal
and restores BopBop when you quit Neovim. A failed tmux split also uses that
same-terminal path and shows the tmux error.
Requires Neovim 0.11+. `bopbop editor list` lists live sessions;
`bopbop editor --connect SESSION /absolute/project/file` connects explicitly.

Type `/editor` to browse the workspace above the input. **↑/↓** move through
folders and files, **→/Tab/Enter** enter a folder, **←** goes to its parent,
and **Enter** opens a selected file. A short file preview follows the selected
row. Type part of a name to filter; adding `/` browses that folder. **Esc**
closes the browser and restores chat history navigation. The input stays in
place. A complete path still opens directly, including names with spaces.
`/help` shows these controls and the Neovim review keys.

Select with **v/V**, then **`<leader>be`** to explain or **`<leader>br`** to
request an edit. Enter a prompt; the response opens a review split. In the
review, **`<leader>ba`** applies and **`<leader>bd`** discards. **u** undoes;
files are never silently saved. The winbar shows the available keys and state.
**`<leader>bc`** cancels; **`<leader>bt`** retries a retained request.

Selections include unsaved text and use the same conversation/model. These
focused turns disable tools so a proposed edit cannot bypass review. Changes
to the original buffer invalidate a proposal. F4 shows the exact model request;
F3 shows its answer/diff. Existing mappings are preserved and keys configurable.
See [Neovim setup, keys, boundaries and recovery](docs/neovim.md).

#### Reading the trace

Each model step shows a live progress line, then Ollama's exact input/output
token counts, generation rate, and timings. Tool calls show their arguments
and a short result preview. `/stats` sums input tokens across model calls;
replayed conversation history is counted again on each call. `stream chunks`
and `thinking chars` are activity indicators, not token counts.

Color is automatic in a terminal. Use `--color never` or `NO_COLOR=1` for plain
text. Startup shows the original pearly BopBop character blinking, smiling,
and winking in a short facial greeting.
Use `bopbop local --banner static` for a still portrait or `--banner off` for
a compact text header. The portrait adapts to terminal width; redirected
output and server JSONL use their normal text formats. Art is bundled with
the CLI and requires no additional packages.

`read_file` and `write_file` stay within the selected workspace, while
`run_command` executes with your user permissions and has no approval step.
Its default timeout is 30 seconds. Long workflows can set `timeout_seconds`
explicitly, up to 21,600 seconds; the agent waits for the result. Cancellation
and timeout stop the command's process group. `max_output_chars` selects the
returned output tail (256–12,000 characters, default 12,000); use a smaller
tail for verbose progress logs to keep the next model request small. For example:

```text
/call run_command {"command":"python3 scripts/long-task.py","timeout_seconds":3600,"max_output_chars":3000}
```
Use a workspace you trust. A selected local `--profile` exposes only that
pack’s native tools and its short instructions.

## Context packs

See the [Ice dashboard pack audit and tool plan](docs/ice-dashboard-pack-plan.md)
for the original audit. The [Ice pack](packs/ice-dashboard/README.md) is now
implemented with four native tools and compact read-only dashboard APIs.

```bash
bopbop pack install ./packs/ice-dashboard
bopbop local --profile ice
bopbop local --packs ice           # workspace tools plus the Ice API
bopbop agent codex --profile ice    # or claude
```

To include Ice in every normal terminal launch, set `BOPBOP_CLI_PACKS=ice`
in `~/.bopbop/env` and relaunch `bopbop`. `/tools` labels the pack as
`ice-dashboard (ice)` and lists its four `ice_*` API tools.

See [native pack tools](docs/pack-tools.md) for schemas, handlers, MCP, profile
selection and server integration; [validation](docs/ice-pack-validation.md)
records the test results and measured response times.

A **pack** is an installable capability bundle for your agent — a git repo
(or local dir) shaped like:

```
pack.toml             # name, description, fragment, tools, profile, optional hook
CLAUDE.fragment.md    # spliced into your context CLAUDE.md (managed markers)
tools.json            # optional native tool schemas + handler declarations
scripts/ …            # handler code and documented helpers
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

`pack.toml` keys (flat TOML values):

| key | meaning |
|---|---|
| `name` | kebab-case pack name (required) |
| `description` | one-liner shown in `pack list` |
| `fragment` | path to the CLAUDE.md fragment to splice |
| `profile` | optional short alias for explicit specialist selection |
| `tools` | optional protocol-v1 tool manifest, e.g. tools.json |
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
`--dangerously-bypass-approvals-and-sandbox`; Ollama: local tools run with your
user permissions). The agent has **full access to
your machine** — that's the point, and the risk.

- **Sender allowlist is mandatory.** The Signal channel refuses to start
  without `SIGNAL_ALLOWED_USERS`; inbound messages from senders not on it
  are dropped before the agent sees them. (Your own Note-to-Self always
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
| `BOPBOP_AGENT_HARNESS` | `claude` | server backend: `claude`, `codex`, or `ollama` |
| `BOPBOP_AGENT_PROFILE` | unset | installed pack/profile for all server harnesses |
| `BOPBOP_CLI_PACKS` | unset | extra installed native packs for local terminal launches only, comma-separated |
| `BOPBOP_CLAUDE_BIN` | `claude` | Claude Code binary |
| `BOPBOP_CLAUDE_MODEL` | `sonnet` | model per turn |
| `BOPBOP_CODEX_BIN` | `codex` | Codex CLI binary |
| `BOPBOP_CODEX_MODEL` | unset | optional Codex model override; unset uses Codex config |
| `BOPBOP_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama API base URL |
| `BOPBOP_OLLAMA_MODEL` | `gpt-oss:20b` | local model for terminal and server |
| `BOPBOP_OLLAMA_CONTEXT` | `16384` | request context window |
| `BOPBOP_SIGNAL_ENABLED` | — | `1` to enable the Signal channel |
| `SIGNAL_HTTP_URL` | `http://127.0.0.1:8080` | signal-cli-rest-api |
| `SIGNAL_ACCOUNT` | — | your number, E.164 |
| `SIGNAL_ALLOWED_USERS` | — | comma-separated allowlist (required) |
| `BOPBOP_REQUIRE_BEARER` | `0` | gate chat and settings with a bearer token |
| `BOPBOP_PWA_ENABLED` | `0` | serve the optional app after `bopbop web build` |

## Niceties

- **Session warmth**: consecutive messages resume the same harness-specific
  session (1h idle window or 20 turns, whichever lasts longer), so "and what
  about the second one?" works. Switching harness/model/pack/context starts fresh; `/reset`
  also starts fresh.
- **Attachments**: send a photo; the local attachment path is included for the
  selected agent to inspect.
- **Typing indicator** while the agent works; long replies are split at
  paragraph boundaries.
- **Test without a phone**: `curl -X POST http://127.0.0.1:8090/api/test/signal-inject -H 'Content-Type: application/json' -d '{"message":"hi"}'`
  (localhost-only) pushes a message through the full production path.
- **HTTP API**: `POST /api/chat {"message": "..."}` streams NDJSON —
  build any frontend you like on it.

## Browser app (PWA)

The optional Svelte app shares the server's configured Claude, Codex, or Ollama
harness and installed context packs. It uses the CLI's Pearl character cutout
with aligned expressions,
streams Markdown replies, saves a rolling transcript and draft on your device,
and shows expandable tool calls, token counts, and timing measurements.

```bash
# Node.js 22+ and npm are needed only to build/develop the browser app.
npm ci
bopbop web build
# Set BOPBOP_PWA_ENABLED=1 in ~/.bopbop/env, then:
systemctl --user restart bopbop.service
# Open http://127.0.0.1:8090/
```

For phone access, use your existing trusted HTTPS private-network/proxy setup.
With `BOPBOP_REQUIRE_BEARER=1`, enter the access token in the app's Settings;
it stays in tab session storage, separate from saved history. The offline shell
shows saved messages but cannot run the agent without the server.

Open **Settings** to choose Claude Code, Codex, or local Ollama; select an
installed local model, a native context pack, and the local context size. For
wrapper harnesses, enter a supported model ID or leave it blank for the
installation default. Changes persist across restarts and apply to new browser
and Signal turns. Running replies retain their original configuration. The
phone layout has a scrollable settings sheet, 44px controls, an expanding
composer, and a compact character during conversations.

Development: `bopbop web dev` (loopback Vite server, API proxied to port 8090).
Verification: install Python test dependencies, run
`npx playwright install chromium webkit`, then `bopbop web test`.
[PWA setup, architecture, telemetry, and testing](docs/pwa.md).

## Status / roadmap

Extracted from a personal setup that's been running for a while; the
public packaging is young. Linux/systemd only for now. Roadmap: macOS
(launchd), more channels, and PWA voice/image input.

## License

MIT
