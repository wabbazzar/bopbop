# How BopBop selects prompts

## Native Ollama

`bin/bopbop-local.py:system_prompt()` selects **one base prompt**. The dashboard
branch assigns a new `parts` list; it replaces the workspace base, not appends to it.

| Launch configuration | Base prompt | Workspace instructions | Tools |
| --- | --- | --- | --- |
| No profile, no packs | Workspace assistant | Yes | Built-in workspace tools |
| `--packs PACK` without a profile | Workspace assistant | Yes | Built-ins plus selected pack tools |
| `--profile PROFILE` | Dashboard assistant | No | Selected pack tools only |

A profile also selects its pack. Additional `--packs` can select more packs.
Pack fragments are appended to whichever base was chosen. Tool calls such as
`ice_query` with `report=health` or `report=services` return evidence; neither
selects a system prompt. `ice_query` is only callable when its pack is enabled.

### Where the selection comes from

- The Python CLI parses `--profile`, defaulting to `BOPBOP_AGENT_PROFILE`.
  Explicit flags override defaults. Empty `--profile ''` means workspace mode.
- `--packs` defaults to `BOPBOP_AGENT_PACKS`. The `bin/bopbop` local launcher
  can substitute the CLI-only `BOPBOP_CLI_PACKS` default.
- The shell launcher consumes `--profile` itself for Claude/Codex wrappers.
  For **local/Ollama**, it forwards the argument to the Python CLI unchanged.
  The wrapper-only parsing condition does not mean Ollama lacks profile support.
- Profiles are installed pack names or unambiguous profile aliases resolved by
  `bin/bopbop_packs.py:Registry`. `ice` is the alias declared by `ice-dashboard`.
  Naming a nonexistent profile is an error, not a new arbitrary persona.

### What gets appended

In workspace mode, `BOPBOP.md`, when present, replaces loading `AGENTS.md`,
`CLAUDE.md`, and `personality.md`. Otherwise existing files from that list are
loaded. Workspace skill paths are indexed; their full bodies are not automatically
loaded. Profile mode skips those workspace instruction files and the skill index.
Both modes append selected pack fragments and a generated provenance block
listing the actual mode, files, packs and tool names.

Templates under `templates/context/` are installation scaffolding. Their presence
in this repository does not cause them to be loaded into every request.

## Inspect a freshly composed prompt without inference

From this repository, use the native Python entry point:

```sh
python3 bin/bopbop-local.py --show-prompt --profile '' --packs ''
python3 bin/bopbop-local.py --show-prompt --profile '' --packs ice-dashboard
python3 bin/bopbop-local.py --show-prompt --profile ice
```

The last two examples require the pack to be installed in the selected context
directory (`--context-dir` can override it). These commands print JSON containing
`system_prompt` and tool schemas. They do not query Ollama, execute pack handlers,
send messages, install packs, or change a running session. They cannot be combined
with a question or session transport flags. They still validate the configuration.

A preview describes **new assembly from current files/configuration**. It does
not prove what an already-running conversation contains. F4 Context shows the
managed terminal's captured outbound request. Refresh preserves conversation
messages, including the old system prompt; reset retains the system message too.
Start a new session to load new system instructions.

## Browser, Signal, and external harnesses

Browser and Signal use saved server settings with environment defaults. The
server freezes settings per turn. `server/runner.py:_build_args()` passes the
chosen profile to native Ollama via `--profile`; CLI configuration is separate.

Claude Code and Codex use their own harness instructions. Their BopBop pack
integration lives in `bin/bopbop_packs.py:wrapper_options()`: Claude receives
`--append-system-prompt`, while Codex receives `developer_instructions`, together
with per-invocation MCP configuration. These are not the native Ollama base
prompt branches above.

Vim completion is a separate request path. Its default Qwen model uses raw
fill-in-the-middle input from the current buffer, not the chat system prompt.
