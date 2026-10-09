# 3. Neovim selection and prompted edit connection

Status: Queued. Start after the ticket 2 test ride.

## Outcome

The user edits in a real Neovim split and sends a selection plus an explicit
prompt to the current BopBop session. BopBop can explain it or propose a change
that the user reviews and applies in Neovim.

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
- Cancel/disconnect/reconnect without losing the editor buffer or misrouting a
  response to another BopBop session. Cover bridge endpoint cleanup/ownership.
- Run a real local-model explanation and a small prompted edit, checking the
  produced diff and a relevant code check. Keep private transcripts outside git.
- Document install/configuration, keys, session routing and limitations.

## User test ride

Open a small function in Neovim, select it, ask BopBop to explain it, then ask for
one small change. Inspect the diff, accept it, undo it, and try editing while a
suggestion is in flight. Hand off and await feedback before ticket 4.
