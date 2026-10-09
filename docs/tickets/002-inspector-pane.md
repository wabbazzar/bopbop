# 2. Optional terminal inspector

Status: Implemented on 2026-10-09; awaiting test ride before ticket 3. The user
authorized both slices without an intermediate approval checkpoint.

## Delivery slices

1. Anchored input and upward menus: optional prompt_toolkit renderer, bounded
   conversation viewport, shared input history, cancellable worker, existing
   schema-driven help. Verify physical cursor rows and transcript restoration.
2. Inspector: Result, Context, Docs and File/Diff with explicit attachments,
   provenance, pinning, navigation and retained-event limits. Continue directly
   after slice 1 verification; stop for a test ride before ticket 3.

## Outcome

Conversation and input remain usable while a right pane shows tool docs, a
selected result, an exact model request, or a file/diff snapshot. Narrow terminals
can toggle the inspector as a full-width view. Plain CLI and server transports
remain supported by the same agent engine.

## Implementation scope

- Add an optional split UI (proposed launch: `bopbop local --ui split`). Use
  `prompt_toolkit` as the initial candidate; record the dependency and install
  path. Keep the execution engine usable without importing the optional UI.
- One renderer owns terminal output. Deliver typed events/state for assistant
  output, tool start/result, timings, usage, errors and completion. Run blocking
  work away from the input/render loop and define cancellation behavior.
- Inspector views: Docs, Result, Context, File/Diff. Provide focus switching,
  independent scrolling/search, pane resizing/toggling and pin/unpin behavior.
  A pinned item must not be replaced by the next tool event.
- Store complete bounded tool results with call IDs, arguments, timestamps,
  duration, state and truncation information. Display all returned bytes; never
  imply API-omitted rows are locally available.
- Snapshot the exact outbound request after history pruning for each model
  step. Show instructions, tools, messages and settings, plus a record of removed
  history. Distinguish measured token usage from estimates. Context inspection
  describes submitted data and exposed events, without inferring internal model
  reasoning.
- A file view is an identified snapshot with path/range/version. Reading it in
  the UI does not silently attach it to the model. Explicitly attaching selected
  text/result rows shows the source, scope and size in the next prompt.
- Initial file/diff view is for reading/review. Interactive editing belongs to
  ticket 3. Reuse ticket 1's metadata and references without tool-specific UI.
- Bound retained results/requests and explain evictions. Keep private captures
  out of the repository; establish any opt-in session/export persistence.

## Existing seams

`bin/bopbop-local.py` currently owns `chat`, `turn`, `run_tool`, pruning and
terminal printing. JSONL events already support the server, but result events
currently contain previews and there is no exact-request snapshot store.
`bin/bopbop_tool_ui.py` owns generic reference formatting. Build the new event
and inspection model independently of terminal widgets.

## Acceptance and verification

- Render a slow streaming fixture while typing, switching panes and scrolling;
  input remains responsive and the cursor/focus stays predictable.
- Compare captured request snapshots byte-for-data with a fixture HTTP server's
  received payload, including tool schemas and a forced history-pruning case.
- Inspect success, error, stale and truncated results; pinned results survive
  subsequent events. Source/full-result/attached-selection distinctions remain
  visible. Sensitive headers are not exported as context.
- Verify selection attachments actually reach the next request, while merely
  viewing a file/result does not change that request.
- Cover cancellation, terminal resize, narrow dimensions, Unicode, large bounded
  results and cleanup. Re-run applicable CLI, pack and JSONL/server regressions.
- Verify both optional-UI installation and a launch without the dependency.

## User test ride

The optional renderer is installed on the development machine. Run
`bopbop local --ui split` or launch `bopbop` and press F9. F2–F5 choose Docs,
Result, Context and File; F6 changes focus; F7 pins; F8 attaches selected body
lines. Alt-Left/Right browse retained snapshots; Alt-minus/plus resize the pane.
Below 100 columns the inspector uses the conversation viewport and F9 toggles
back. `/inspect file PATH`, `/inspect diff PATH` and `/attach START:END` provide
equivalent explicit navigation/attachment flows. `/detach` clears attachments.

Run a direct Ice call, pin its full result, ask a follow-up and inspect what was
sent. Inspect a local file, attach only a selected range, and verify the next
request. Resize the terminal and continue typing during generation. Commit and
hand off this build; wait for feedback before ticket 3.

## Implementation and verification

- Slice 1 committed as `ff865fc`: anchored composer and upward overlays, with
  real terminal tests confirming fixed cursor rows and restored underlying text.
  Full regression gate at that point: 171 tests passed.
- `bin/bopbop_tui.py` owns rendering and a cancellable worker process. Worker
  stdout/stderr is captured inside that process; model streams, checkpoints,
  tool events and request snapshots reach the renderer through a queue.
- `bin/bopbop_inspector.py` owns snapshots and retention without importing the UI
  dependency. Requests freeze the actual serialized HTTP body after pruning.
  Tool records retain returned content, call IDs, timing and source telemetry.
- Snapshot limits: 32 records / 8 MiB with pin preservation and eviction notices;
  files/HEAD versions up to 256 KB; attachments up to eight selections / 32 KB.
  State is in memory only. File inspection is asynchronous and workspace-bound.
- Read-only file views and HEAD diffs are implemented; absent/untracked HEAD
  versions report an error. Editing and model completions remain tickets 3–4.
- The managed screen uses application scrollback. Native terminal scrollback
  remains available with `--ui plain`; no optional package is imported by that
  engine path. Installation is `bopbop ui install` into the root `.venv`.
- Tests compare snapshots with the fixture server's actual requests, force
  pruning, check snapshot immutability, tool evidence/source limits, pinned
  eviction behavior, file versions/diffs, selection-only attachments, live
  streaming with simultaneous typing, keyboard selection, history, resize,
  terminal cleanup and cancellation of pack process groups.
- Final gate: **181 tests passed** across server/pack/CLI/UI suites; one existing
  Starlette/httpx deprecation warning. Bash syntax and whitespace checks pass.
  The installed launcher was exercised from `/tmp` with the real Ice reference
  and parameter help. A real terminal capture with fixture result data was
  visually inspected at `/tmp/bopbop-inspector-preview.png`.
