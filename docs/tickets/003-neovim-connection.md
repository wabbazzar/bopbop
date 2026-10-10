# 3. Neovim selection and prompted edit connection

Status: Implemented; awaiting the ticket 3 user test ride. Ticket 4 remains queued.

## Outcome

The user edits in a real Neovim split and sends a selection plus an explicit
prompt to the current BopBop session. BopBop can explain it or propose a change
that the user reviews and applies in Neovim.

## Selection and key hints

User direction: Neovim Visual mode is the primary selection mechanism. Preserve
the visible hotkey guidance introduced in the inspector.

- Support characterwise `v` and linewise `V` selections for explain/edit actions.
  Selecting text alone sends nothing. Capture the exact selection, including
  unsaved text, only when the user explicitly submits a question or edit request.
- Treat blockwise Ctrl-V selections explicitly: explanations may use labeled
  per-line spans; initially reject blockwise replacement with a clear hint to
  use `v` or `V`. Never silently replace the rectangle's bounding range.
- Keep whole-buffer requests a separate explicit action. No active selection
  must not silently expand the request to the entire file.
- Show context-sensitive hints in the editor as well as connection status in
  BopBop. Hints must reflect the actual configured mappings, not hardcoded keys.
  Include selected file/range/size before submission and show whether the bridge
  is connected, working or awaiting review.
- Proposed defaults under `<leader>b`: Visual mode `e` explains and `r` requests
  an edit; a review buffer offers `a` to apply and `d` to discard; an active
  request offers `c` to cancel. Make these configurable, scope review actions to
  the review buffer, and preserve existing user mappings. Do not intercept
  ordinary Vim movement or selection keys.
- Retain the selection and prompt if disconnected or submission fails. A model
  response opens a review; it never applies an edit automatically.

## Implementation scope

- Connect an adjacent Neovim instance to the same BopBop session through a local
  bridge. Define session selection, ownership, connection/disconnection and
  workspace checks. Prefer the existing editor UI and Neovim's public API.
- Provide explicit actions to send a selection/current buffer with a question
  and to request a replacement for the selected region. Carry file path, range,
  language, snapshot content and buffer version, including unsaved content.
- Reuse inspector context/result events so the user can inspect the exact
  selection and prompt given to the model.
- Return a reviewable diff/replacement. Apply only on the user's editor action,
  through a normal undoable buffer edit. Reject or regenerate stale suggestions
  if the source region changed while the model was working. Do not overwrite an
  unrelated dirty buffer or silently save the file.
- The explain/edit operation belongs to BopBop's current local harness and
  selected model. Preserve tool permissions and explicit action boundaries.
- Provide clear keybindings, connection status, cancellation and failure
  recovery; keep terminal/editor cleanup reliable.
- Automatic suggestions on keystrokes are deferred to ticket 4's research and
  a subsequent implementation decision. This ticket uses explicit prompts.

## Existing seams and research

Use ticket 2's session/event/context model, `bin/bopbop-local.py`'s agent loop,
and Neovim's public RPC/buffer APIs. Study Aider's editor workflow and
CodeCompanion's CLI/selection integration as interaction precedents; keep shared
session identity and context behavior explicit.

## Acceptance and verification

- Exercise a real headless Neovim process with fixture model replies: select a
  range, send it, inspect the exact request and apply an accepted replacement.
- Verify unsaved text, Unicode, multiline selections, multiple buffers, version
  conflicts, rejected diffs and undo. A late response cannot overwrite new work.
- Verify exact characterwise/linewise boundaries, reversed selections and
  blockwise handling. Confirm the model receives only the explicitly selected
  evidence plus disclosed session context. Test mapping overrides and verify
  that hints match the active selection/request/review state.
- Cancel/disconnect/reconnect without losing the editor buffer or misrouting a
  response to another BopBop session. Cover bridge endpoint cleanup/ownership.
- Run a real local-model explanation and a small prompted edit, checking the
  produced diff and a relevant code check. Keep private transcripts outside git.
- Document install/configuration, keys, session routing and limitations.

## User test ride

Open a small function in Neovim, select it with `v` or `V`, use the visible key
hints to ask BopBop to explain it, then ask for
one small change. Inspect the diff, accept it, undo it, and try editing while a
suggestion is in flight. Hand off and await feedback before ticket 4.

## Delivered (2026-10-10)

- Private session-owned Unix socket bridge in `bin/bopbop_editor.py`, integrated
  with the managed terminal's existing worker, conversation and inspector.
- `bopbop editor list`, explicit `--connect SESSION FILE`, and `/editor FILE`
  (adjacent tmux pane, or a copyable launch command for another terminal).
- Bundled Neovim 0.11+ Lua module: exact Visual/line/block explanation selection,
  prompted edits, configurable context-sensitive hints, review, explicit apply,
  discard, retry and cancellation. Narrow terminals stack source/review.
- Tool-free review turns, structured edit responses, conservative changedtick
  conflicts, ordinary undo and no implicit file save.
- [Setup and protocol boundaries](../neovim.md), including ownership, limits,
  failures, and the distinction between shared history and newly selected code.
- Real headless Neovim tests cover Unicode (including combining characters),
  reversed/exclusive/multiline/empty/block selections, user mappings, actual
  hotkey invocation, unsaved buffers, other buffers, stale edits, apply/discard,
  undo, cancellation/retry, reconnects, socket ownership and cleanup.
- End-to-end PTY + fixture model tests verify shared session history, exact
  selected evidence, model settings, inspector visibility and disabled tools.
- A real GPT-OSS 20B run explained a function, proposed a constant-return edit,
  applied it, executed it to check the result, undid it, and verified the
  original disk file stayed unchanged. Explanation: 26.0 s including 18.9 s
  model load; next edit: 7.1 s, first answer 4.0 s. These are individual local
  observations, not latency guarantees; a later preview edit took 18.4 s.

The review is complete at the user's explicit apply/discard action; automatic
completion research has not started. Test ride the keys and buffer workflow
before ticket 4.
