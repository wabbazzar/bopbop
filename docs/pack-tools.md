# Native pack tools (protocol v1)

Packs can add real model tool schemas and executable handlers. Installation
validates the manifest before replacing an existing pack. Selection is explicit:

```bash
bopbop pack install /path/to/pack
bopbop pack tools my-pack
bopbop pack call my-pack sample_count '{"window":"24h"}'
bopbop local --profile my-pack
bopbop local --packs my-pack,another-pack
bopbop agent codex --profile my-pack
bopbop agent claude --profile my-pack
```

`--profile` loads the chosen pack's short context and exposes only its native
tools in the local harness. `--packs` adds native tools to the general workspace
agent. A profile may be the pack name or its unique `profile` alias. Missing
profiles, ambiguous aliases and colliding tool names fail before a model call.

For repeat terminal use, set `BOPBOP_CLI_PACKS=ice` (or a comma-separated list)
in `~/.bopbop/env`. Plain `bopbop` and `bopbop local` then add those tools to the
workspace agent. This setting applies only through the terminal launcher;
browser and Signal preferences remain separate. Explicit `--packs` overrides
this default; `--packs ''` disables additions for one launch. Restart an open
CLI to load changes, and run `source ~/.bashrc` if that shell still has an older
alias pointing directly at the Python script.

Claude and Codex receive a per-invocation MCP server and pack instructions.
Their own built-in capabilities remain available. Selecting the Ice profile
**does not sandbox a wrapper agent**. Use the local profile when you want the
four-tool interface without workspace tools.

For the Signal/HTTP server, select the context pack in the browser's Settings,
or set `BOPBOP_AGENT_PROFILE=my-pack` as an env default and restart the service.
Saved browser preferences override those defaults; see [PWA settings](pwa.md#server-settings).
The selected harness uses the same pack. Local
persistent sessions are keyed by workspace, model, profile and pack instruction/
schema fingerprint; changing these starts fresh context. Restart/reset wrapper
sessions when changing their pack configuration.

## Bundle

```text
pack.toml
CLAUDE.fragment.md
tools.json
scripts/handler.py
```

```toml
name = "my-pack"
profile = "my-specialist"
description = "A small specialist"
fragment = "CLAUDE.fragment.md"
tools = "tools.json"
```

Existing instruction-only packs remain supported. Optional `install_hook` and
comma-separated `required_env` retain their existing behavior. Paths are
relative to the pack and cannot escape it through traversal or symlinks.

```json
{
  "schema_version": 1,
  "tools": [{
    "name": "sample_count",
    "description": "Count observations in a supported window.",
    "parameters": {
      "type": "object",
      "additionalProperties": false,
      "properties": {"window": {"type": "string", "enum": ["24h", "7d"]}},
      "required": ["window"]
    },
    "handler": "scripts/handler.py",
    "timeout_seconds": 8,
    "max_output_bytes": 8192,
    "documentation": "Missing observations are unknown, not a zero count.",
    "returns": "JSON with ok, state and count.",
    "examples": [{
      "summary": "Count observations during the past day.",
      "arguments": {"window": "24h"}
    }]
  }]
}
```

V1 supports 1–24 tools per pack, each with at most 16 flat scalar arguments:
`string`, `integer`, `number`, `boolean`; `enum`, `minimum`, `maximum`, and
`maxLength` are enforced. Nested objects/arrays are unsupported. Arguments reject
unknown keys, missing required keys, wrong types, non-finite numbers and control
characters. Tool names must use lowercase letters, digits and underscores.

Optional human reference fields are `documentation` and `returns` (strings up
to 8,000 characters each), and `examples` (up to eight objects containing
`arguments` and an optional `summary` of up to 240 characters). Examples are
validated against the same parameter schema during installation; invalid
examples fail installation. Existing packs need no new fields. The CLI derives
typed signatures, required/optional labels, enums and bounds from `parameters`.
Keep parameter descriptions useful to both humans and the model.

These reference fields appear in `/tools NAME` and the interactive browser but
are excluded from model/MCP schemas and schema fingerprints, so adding human
documentation does not increase prompt tokens or invalidate server sessions.
Changes to parameter descriptions still change the model schema. Reinstall a
pack after editing its source bundle to update the installed reference.

In the terminal, these same declarations power live help as soon as `/call`
contains a complete enabled tool name, even before its arguments form valid
JSON. F1 switches between compact and full reference, Page Up/Down scrolls, and
Esc hides help; F1 restores it. The draft and insertion point remain intact.
New pack tools need no changes to the UI to get this behavior. Builtin tools
likewise own their reference fields in their declarations in
`bin/bopbop-local.py`; the UI consumes the runtime's enabled definitions.

## Explicit calls in terminal chat

```text
/tools ice_query
/call ice_query {"report":"projects","limit":20}
/call ice_query {"report":"health"} :: Summarize what needs attention
```

`/call NAME [JSON object] [:: question]` executes one enabled tool using the same
dispatcher and telemetry as model-selected calls. You can omit the object for
tools with no required arguments. Invalid JSON, disabled tools, wrong types,
unknown keys and missing required values are rejected before execution. JSON
values containing `::` are preserved; the question separator follows the object.
Workspace strings follow their own schemas and accept escaped newlines in file
content; native pack strings retain protocol-v1 length/control-character bounds.

Without a question, the CLI prints the full result without contacting Ollama,
records one tool call and zero model tokens, and adds a normal assistant-call /
tool-result pair to conversation history. Your next question can use that
evidence; several explicit calls can prepare a combined summary. With a
question, the first model request already includes the evidence. The explicit
call counts toward the profile's four-call budget, and the model may call more
tools as needed. Failed tool results remain explicit failure evidence.
`/reset` clears the history; ordinary context pruning still applies.

This syntax is local terminal UI behavior. JSONL server chat and wrapper
harnesses do not interpret these slash commands. Workspace action tools execute
immediately when explicitly requested, using the same permissions as normal
dispatch. For native pack calls outside chat, use `bopbop pack call`.

## Handler transport

The registry starts a fixed Python file, with the pack as its working directory.
One JSON object arrives on stdin:

```json
{"tool":"sample_count","arguments":{"window":"24h"}}
```

Print exactly one JSON object on stdout. Diagnostic stderr stays out of the
model. A result should distinguish missing evidence from zero observations:

```json
{"ok":true,"state":"ready","count":5,"observed_at":"2026-10-07T12:00:00Z"}
```

Timeouts, nonzero exits, malformed/non-finite JSON, and oversized output become
`{"ok":false,"state":"unavailable","error":"…"}`. Timeout range: .05–60
seconds; output cap: 256–65,536 bytes on stdout; stderr is capped separately at 16 KiB. Tool requests are capped
at 64 KiB and supplied through a bounded temporary file, so a handler that never
reads stdin still reaches its deadline. The registry kills the
handler process group after each invocation, including leftover child processes.

Installing a pack means trusting its handler code: handlers inherit your
permissions and environment. An API client can be read-only; arbitrary Python
handlers are not isolated by this protocol. Hooks only run when explicitly
requested with `--run-hook`.

## MCP

```bash
bopbop pack mcp --packs my-pack,another-pack
```

The adapter speaks newline-delimited JSON-RPC on stdio: `initialize`, `ping`,
`tools/list`, `tools/call`. Requests are capped at 64 KiB. It opens no listener
and uses the same validation and handler bounds as local dispatch. Configure it
in another harness with the absolute path to `bin/bopbop-pack.py`, Python 3.11+,
and `--context-dir /path/to/context mcp --packs my-pack`.

## Trace and diagnostics

`/tools` groups enabled tool names/descriptions by workspace and pack, shows
installed native packs that need selection, and opens a reference browser when
stdin/stdout are terminals. Up/down select a tool; left/right page through its
reference; Enter prints the selected reference to scrollback; Esc closes it.
`/tools NAME` prints one reference directly, including the full model schema.
Neither browsing nor inspecting contacts the model or dashboard.
`/call ` and `/tools ` offer enabled tool-name suggestions. Enter on a `/call`
tool suggestion inserts editable example arguments; it does not execute until
you submit the completed command. `bopbop pack tools NAME` prints full argument
schemas as JSON. Native calls show
state, response bytes, source age, truncation, cache status and API time.
Model telemetry distinguishes first activity from first answer, and reports
load, prompt evaluation, generation, token counts and total turn duration.
When Ollama does not report cache counts, the CLI says `cache unreported`; JSONL
includes `cache_measured:false`. A zero counter alone does not prove no reuse.
GPT OSS defaults to low thinking and a 512-token per-call budget for profiles;
use `--think` or `--max-output` to change it. A generation limit stops the turn
without publishing an incomplete answer. Profiles allow four model steps per
turn and four tool calls total; general workspace turns allow twelve model steps.
Old complete turns are removed together before each call. A current profile turn
that exceeds the conservative history byte budget stops explicitly rather than
sending an oversized prompt.

Run the protocol and server tests with:

```bash
server/.venv/bin/python -m pytest server/tests -q
```
