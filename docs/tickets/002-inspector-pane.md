# 2. Optional terminal inspector

Status: Queued. Start after ticket 1 is implemented and the user finishes its test ride.

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

Run a direct Ice call, pin its full result, ask a follow-up and inspect what was
sent. Inspect a local file, attach only a selected range, and verify the next
request. Resize the terminal and continue typing during generation. Commit and
hand off this build; wait for feedback before ticket 3.
