# 1. Live documentation while composing /call

Status: Accepted on 2026-10-09 after parameter filtering and history feedback.

## Outcome

When a user types or selects an enabled tool in `/call`, its documentation
appears under the draft and stays available while JSON arguments and a `::`
question are edited. The reference comes entirely from that tool's definition.

## Scope and behavior

- Recognize an exact enabled tool name without requiring valid/complete JSON.
- Show description, required/optional typed parameters, constraints and a call
  example. No-argument tools explicitly say so.
- Keep help visible after `{`, including when it touches the tool name. Filter
  parameter names and enum/example values at the cursor; show detailed schema
  documentation for the focused parameter. Omit previously supplied parameters
  from the next-key list. The full reference remains available with F1.
- Share session input history across chat and slash commands. Up/Down recalls
  editable lines outside suggestion menus; returning down restores the draft
  and cursor. Keep at most 500 lines in memory, without disk persistence.
- F1 toggles a full reference, including notes, all examples, returns and model
  schema. Page Up/Down scroll the help. Esc dismisses it; F1 restores it.
- Preserve the draft and cursor through help navigation. Fit the panel to the
  terminal; long drafts scroll horizontally without corrupting cursor placement.
- Keep existing name selection and explicit submission semantics. Selecting a
  call inserts editable arguments; it does not execute the call.
- Move builtin reference metadata out of the UI lookup table into the builtin
  tool declarations. Both live help and `/tools` consume the same definitions.
- Human documentation remains excluded from model tool schemas.
- Help performs no handler execution or model/API requests. This ticket adds
  no model-generated suggestions or parameter-value inference.

## Implementation pointers

- `bin/bopbop_prompt.py`: plain line editing and key decoding.
- `bin/bopbop-local.py`: builtin declarations,
  `AgentRuntime` and schema projection.
- `bin/bopbop_tool_ui.py`: definition discovery, shared reference rendering and
  terminal-safe compact help.
- `server/tests/test_cli_tools.py`: PTY and explicit-call regression coverage.
- `README.md` and `docs/pack-tools.md`: user controls and pack author contract.

## Acceptance and verification

1. A newly installed fixture tool gets live help with no UI code changes.
2. Docs appear for a complete name before arguments and during incomplete JSON,
   escaped strings and `::` prompts. Unknown/disabled names have no reference.
3. Types, required flags, enums, bounds and examples reflect the declaration;
   a no-argument tool shows `No arguments` and an empty-object call example.
4. Expanding, scrolling, hiding and restoring help preserve the exact submitted
   string, including Unicode and a long draft in a narrow terminal.
5. PTY tests prove that browsing/editing never starts a handler or network call;
   explicit submission still uses the existing validated dispatcher.
6. Interrupt/exit restores terminal state. Existing plain/piped and JSONL paths
   continue to work. Model schemas contain no human reference fields.
7. Capture a real terminal preview and record the test result below.
8. Partial keys, values, escaped strings and cursor movement select the correct
   parameter details. Recalled calls show help without executing. Navigation
   preserves unfinished input and works across ordinary chat and slash lines.

## User test ride

Relaunch `bopbop`. Type `/call ice_status` and inspect its help before submitting.
Try `/call ice_query`, insert its example, edit `report`, then append a `::`
question. Use F1, Page Up/Down and Esc while editing. Finally submit one call.
Type `/call ice_query{"rep`, then finish the key and type `:"he` to see help
narrow to the matching value. Use Up/Down to recall that submitted call, then
return to your unfinished draft. Try recalling ordinary chat from a blank line.
Report readability, interference with typing, and whether full help is easy to
reach. Finish this checkpoint before starting ticket 2.

## Delivery evidence

- Test-ride feedback update: **169 passed** across the full server/pack/CLI
  suite. Eleven additional checks cover incomplete JSON focus, parameter/value
  filtering, adjacent braces, cursor movement, history recall across chat and
  calls, and exact unfinished-draft restoration. The existing warning below
  remains. The installed Ice parameter panel was visually checked from a real
  PTY capture at `/tmp/bopbop-call-parameter-help.png`.
- Full server/pack/CLI suite: **158 passed**. One existing Starlette/httpx
  deprecation warning remains.
- Ten new checks cover dynamic pack metadata, no-argument references, builtin
  declaration ownership, schema separation, Unicode input viewport positions,
  paging/hiding/resizing with exact draft preservation, and interrupt cleanup.
- Real PTY tests exercise color/plain rendering and disable network/model/tool
  execution while composing. Existing explicit-call tests still pass.
- Actual installed Ice help was inspected in a terminal from an unrelated
  directory. Compact and full previews were captured outside the repository at
  `/tmp/bopbop-live-call-help.png` and `/tmp/bopbop-live-call-help-full.png`.
- No optional TUI library is needed for this delivery. The persistent inspector
  is ticket 2. Model-generated completion research is ticket 4.
