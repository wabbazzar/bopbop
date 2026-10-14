# Architecture and maintenance

BopBop has two ways to run an agent: a local Ollama loop and wrappers around
Claude Code or Codex CLI. The Signal server can select either. The local loop
also runs directly in a terminal. Keep these entry points distinct while
sharing the same user-facing context and pack format where applicable.

## Code map

| Area | Owns | Entry points |
| --- | --- | --- |
| `bin/bopbop` | CLI routing, installation and health commands | `bopbop`, `bopbop doctor`, `bopbop pack` |
| `bin/bopbop-local.py` | Ollama requests, conversation turns, built-in tool dispatch, telemetry | `bopbop local`, server Ollama harness |
| `bin/bopbop_prompt.py` | Plain terminal line editing, history and key decoding | `bopbop local --ui plain` |
| `bin/bopbop_packs.py` | Pack loading, argument validation, handler execution | Local agent and pack CLI |
| `bin/bopbop_tool_ui.py` | Human tool references and explicit `/call` parsing | Plain and managed terminals |
| `bin/bopbop_tui.py` | Optional terminal layout, menus, inspector and editor interaction | `bopbop local --ui single\|split` |
| `bin/bopbop_worker.py` | A turn's process, event queue, timing and cancellation | Managed terminal |
| `bin/bopbop_inspector.py`, `bin/bopbop_editor.py`, `editor/nvim/` | Bounded evidence snapshots and Neovim review protocol | Managed terminal |
| `server/` | Signal and browser API, backend selection, durable sessions and settings | `bopbop run`, systemd service |
| `src/`, `static/` | Browser app | `bopbop web` |
| `templates/`, `packs/` | User context templates and installable capabilities | `bopbop init`, `bopbop pack` |

The local agent needs only Python's standard library. `prompt_toolkit` is
optional and imported only when the managed terminal is selected. The server
and browser app have their own dependencies. New interface work should not
make the local engine depend on either of them.

## Working rules

1. **One declaration per tool.** The tool schema, human documentation,
   examples, and result description live together in a built-in declaration
   or a pack's `tools.json`. Derive model-facing schemas and CLI help from
   that declaration. Keep handler execution and argument validation at the
   engine/pack boundary.
2. **Keep the model loop independent of presentation.** The plain terminal,
   managed terminal, Signal, and browser may display events differently. Do
   not duplicate tool dispatch, conversation rules, or model requests in a UI.
3. **Make evidence and effects visible.** Tool calls report arguments, result,
   status, and timing. Inspector views do not silently enter model context;
   attaching evidence is explicit. Editor changes require review in Neovim.
4. **Put data where its lifetime belongs.** User configuration and sessions
   live under `~/.bopbop/`. Keep disposable local probes in ignored `tmp/`.
   Preserve reusable experiments and conclusions under `docs/research/`.
   Keep credentials and machine-specific values out of the repository.
5. **Add a boundary only when it removes a responsibility.** Prefer a small
   function in an existing module to another abstraction. Extract a module
   when it gives a subsystem a clear owner and helps both code and tests.

## Change checks

- Changes to the local engine or terminal: run
  `server/.venv/bin/pytest -q server/tests` (or install
  `server/requirements-dev.txt` first). The PTY tests
  cover real terminal painting, navigation, and worker cancellation.
- Changes to the browser: run `npm run check`, `npm test`, and the affected
  Playwright checks.
- Before committing: `git diff --check` and
  `bash scripts/leak-check.sh --staged`. CI runs Python, shell, browser, and
  leak checks.

The terminal calls the same engine as the server. It owns one `TurnWorker`
for the active turn; process signaling and reaping belong to that worker.
Plain and managed terminals share command filtering in `bopbop_tool_ui.py`.
Both display declarations owned by the engine or enabled packs.

The server runner drains both output pipes and emits heartbeats during silent
work. Workspace-specific instructions belong in the workspace's `BOPBOP.md`
or skill documents. The harness provides generic tools and timeouts; media
workflows and their protection rules stay in the owning workspace.

The file browser, inspector, and key bindings still live together in the
managed terminal. Extract them when a change gives them independent state or
behavior that needs an owner, rather than splitting by line count alone.
