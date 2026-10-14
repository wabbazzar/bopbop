# Neovim co-driving

A real Neovim process connects to an **explicit local BopBop terminal session**.
Visual selections include unsaved text. Explain/edit requests share that session's
conversation, model, context size, thinking setting and generation limit.
A response opens a review; only your editor action can apply it.

## Start

Requires Linux/macOS, Python 3.11+, Neovim **0.11+**, Ollama, and the optional
managed terminal (`bopbop ui install`). No Neovim package manager or Python
Neovim package is needed. The wrapper harnesses and plain CLI do not host this
bridge.

```sh
# Terminal 1, in your project:
bopbop local --ui single --workspace .
```

In BopBop, type `/editor` to browse the workspace in a menu above the fixed
input. **↑/↓** select a folder or file, **→/Tab/Enter** enter a folder, **←**
returns to its parent, and **Enter** opens a selected file. The selected file
shows a short preview. Type part of its name to filter, or type `dir/` to
browse that folder. **PageUp/PageDown** move through longer listings; **Esc**
closes the menu and restores normal chat history arrows. Hidden names appear
when you start the filter with `.`. Existing full paths still work:
`/editor src/example.py`. You can enter a new filename directly. `/help`
shows the browser and editor review keys.

Inside **tmux**, opening a file starts a real Neovim pane beside BopBop.
Outside tmux, Neovim opens in the current terminal; `:q` returns to BopBop.
The BopBop session and editor bridge keep running while Neovim is visible.
If a tmux split fails, BopBop reports the error and opens Neovim in the
current terminal. Paths containing spaces work in the browser; quote them if
you type a full path explicitly.

The terminal launcher preserves your shell's tool search path ahead of paths
from the service env file. New tmux editor panes receive the same path and
private bridge directory as BopBop. Neovim availability and version are checked
before hiding the terminal. Missing/outdated editors are reported in the
conversation; startup exits remain visible after the screen is restored.
To choose a specific installation, set `BOPBOP_NVIM_BIN=/path/to/nvim` before
starting BopBop. After updating launcher settings, quit and reopen the CLI so
the running process receives the new environment.

In the BopBop pane, drag across conversation or inspector text to select and
copy it. With a reader focused, `y` copies the selection or cursor line.
This uses `wl-copy`, `xclip`, or `pbcopy` when available, then tries the
terminal's OSC 52 clipboard. In tmux, enable mouse mode so drags reach BopBop;
Shift-drag selects through the terminal instead.

You can also choose a session explicitly from any directory:

```sh
bopbop editor list
bopbop editor --connect FULL_SESSION_ID /absolute/project/src/example.py
```

The launcher adds the bundled Lua module to Neovim's runtime path and loads
your normal editor configuration. It does not modify that configuration.
To open the workspace directory itself, run
`bopbop editor --connect SESSION /absolute/workspace`. Both existing files and
named, unsaved new files inside the workspace are supported. Directory listings
and previews stay local; browsing sends no text to the model.

## Select → prompt → review

1. Select with **v** (characters) or **V** (lines).
2. Press **`<leader>be`** to explain or **`<leader>br`** to request an edit.
3. The prompt shows the file, line range and byte count. Enter your question.
   Cancelling the prompt or selecting alone sends nothing.
4. The response appears in the same workbench window. An edit shows a read-only
   unified diff; an explanation is labeled **Answer**. Source buffers retain undo.
5. In an edit review, **Enter** applies and **q** discards. In an answer, **q** closes.
   **`<leader>ba`** / **`<leader>bd`** remain available.
   After applying, **u** undoes. **`:w`** is still your separate save action.

Neovim's default leader is backslash. With the selection still highlighted,
press **backslash**, then **b**, then **e**, in quick succession: `\be`.
These are three keys in the editor, without Enter or a leading colon. If you
pause longer than your Vim mapping timeout (normally one second), the sequence
expires. For a command you can type slowly, press **:** while selected, type
**BopbopExplain**, then **Enter**. Vim inserts the selection's `'<,'>` range
automatically. Both paths open a question prompt; type your question and press
**Enter** to send it. Use `\br` or `:BopbopEdit` for a proposed replacement.
The winbar changes with Visual mode, an active request and a review. It shows
installed keys, or command alternatives when a user mapping occupies a key.
Normal Vim movement, **Ctrl-D/U**, insert mode and editing remain native.

### Inspector keys while editing

**F2–F9** work in Neovim's Normal, Visual and Insert modes. They control the
connected BopBop session through the same socket, without a model request:

| Key | Action |
|---|---|
| F2 | Filter tools by name or description; ↑↓ choose; Enter opens documentation |
| F3 | Pending answer/edit review, otherwise the latest tool result |
| F4 | Exact model request and telemetry |
| F5 | Return to the current source file |
| F6 | Switch between chat input and the workbench in tmux |
| F7 | Pin/unpin the displayed evidence snapshot |
| F8 | Attach selected source text or **V**-selected evidence body lines |
| F9 | Hide/reopen the workbench without losing buffers or undo |

Docs, results, context, file previews and reviews share one Neovim window.
In a file preview, **e** opens the source at the current line. **q** returns
from evidence to the source file. Use **V** then `\be` to ask about selected
numbered evidence lines; open the source with **e** before requesting an edit.
Opening another file reuses the same editor; native `:buffers` / `:buffer`
remain available. Browsing does not send content to the model.

F9 parks Neovim in a tmux window named `bopbop-workbench`. It preserves dirty
buffers even if chat exits; use normal Neovim save/quit commands there.
Outside tmux, `:q` returns to BopBop when sharing one terminal. F6 explains
that limitation. Restart both processes after upgrading the plugin or terminal.
A separately connected editor retains its traditional review split.

Existing function-key mappings are preserved. The winbar shows command
alternatives for occupied F6/F9 keys: `:BopbopFocus` / `:BopbopPane`. Other
alternatives are `:BopbopDocs`, `:BopbopResult`, `:BopbopContext`, `:BopbopFile`,
`:BopbopPin` and `:BopbopAttach`. Customize the corresponding `keys` entries
(`docs`, `result`, `context`, `file`, `focus`, `pin`, `attach`, `pane`) in setup.

| Action | Default / command |
|---|---|
| Explain selection | Visual `<leader>be`, `:'<,'>BopbopExplain` |
| Request selection replacement | Visual `<leader>br`, `:'<,'>BopbopEdit` |
| Apply / discard | Review only: `<leader>ba` / `<leader>bd`, `:BopbopApply` / `:BopbopDiscard` |
| Cancel model request | `<leader>bc`, `:BopbopCancel`; BopBop's Ctrl-C also works |
| Retry retained selection and prompt | `<leader>bt`, `:BopbopRetry` |
| Reopen a closed review | `:BopbopReview` |
| Explicit whole buffer | `:BopbopBufferExplain` / `:BopbopBufferEdit` |
| Forget retained request | `:BopbopForget` (cancel active work first) |
| Reconnect / select another session | `:BopbopConnect FULL_SESSION_ID` |
| Inspect status and current keys | `:BopbopStatus` |

**Ctrl-V blocks** support explanations, with individual spans disclosed to the
model. Block replacement is rejected with a `v`/`V` hint. Virtual selections
that require padding or cutting a displayed character cannot be replaced;
select actual characters or full lines instead. No selection never silently
means the whole buffer.

## Existing Neovim configuration

For your usual Neovim installation, add the repository's `editor/nvim` directory
to `runtimepath`, then configure the module in `init.lua`:

```lua
vim.opt.rtp:prepend('/path/to/bopbop/editor/nvim')
require('bopbop').setup({
  keys = {
    explain = '<leader>be', edit = '<leader>br',
    apply = '<leader>ba', discard = '<leader>bd',
    cancel = '<leader>bc', retry = '<leader>bt',
  },
  hints = true, -- append contextual hints to the winbar
})
```

Choose any mapping, or set a key to `false` to use commands only. Existing maps
are preserved; apply/discard maps exist only in review buffers. Setup is
idempotent, so the launcher honors configuration already loaded in `init.lua`.
Run `:BopbopConnect ID` after setup to select a running terminal session.

## Context, tools and telemetry

The **new evidence** consists of the exact selected text, path, language,
zero-based byte range, selection mode, buffer changedtick and explicit prompt.
Block requests include per-line positions. Line/buffer text is joined with
newlines without an implicit final newline; its range ends at the next row.
A character replacement preserves the surrounding bytes. A line replacement
uses each newline-delimited replacement line literally (an explicit trailing
newline creates an empty line). Empty linewise replacement deletes the selected
lines; Neovim retains an empty line if the entire buffer is deleted.

The model also receives the existing conversation and workspace instructions,
subject to the same normal history pruning. Other buffers and unselected text
are not read automatically. Pending inspector attachments remain pending for
the next ordinary chat request; editor selections do not consume them.

For these focused requests, tool execution is **disabled for the entire turn**.
Even an unexpected model tool call is rejected before dispatch. This narrower
review boundary does not alter the session's normal tool permissions. Edit
replies use Ollama's structured-output schema and are validated before review.
Malformed, oversized or incomplete replacements are never applied.

The workbench shows the source under **F5**, the exact model payload and usage
under **F4**, and the explanation/diff under **F3**. Selection snapshots remain
available through `/inspect file`. Its transcript streams the
response and reports tokens, first activity/answer latency, generation rate and
total time. The header shows editor connection/work/review state. Apply/discard
acknowledgements appear in the transcript when connected. The model history
records a *proposal*, not a claim that the editor saved or accepted it.

## Ownership and recovery

Each managed TUI creates a private Unix socket and metadata descriptor under
`/tmp/bopbop-editor-UID` (directory mode 0700; files/socket mode 0600). Override
with a **short, private directory** in `BOPBOP_EDITOR_DIR`, shared by both
processes. Unix socket path length limits apply. The bridge never listens on a
network port. Metadata contains session ID, workspace, PID and model; no code,
prompt or response is written there.

Clients select the full session ID; there is no implicit "latest session".
Request IDs belong to an editor client, repeat submissions are idempotent, and
poll/cancel cannot target another client's request. Only files resolving inside
that session's workspace are accepted, including symlink checks. This is a
same-OS-user interface, not an isolation boundary against other processes owned
by that user.

Only one model request can run per session. Busy, disconnected or failed
submissions retain their captured selection and prompt **in the same Neovim
process**; retry after reconnecting. Cancel stops the worker, retains the
selection/prompt and allows a fresh retry. Another request requires discarding
or forgetting the previous one. Neither process persists editor requests for
recovery across application restarts.

Apply verifies buffer identity, name, changedtick and the original selected
text. **Any intervening source-buffer edit**, even elsewhere or an edit later
undone, requires discarding and selecting again. Changing another buffer is
safe. A late response never overwrites new work. Review buffers cannot be
edited into a different replacement; revise the prompt and request again.

There are limits of **32 KiB selected text**, **8 KiB prompt**, **32 KiB
replacement**, **256 KiB wire message**, and **16 retained requests per session**.
Graceful terminal exit removes its endpoint. After a forced kill, stale
metadata may remain but `editor list` ignores endpoints that do not answer.

## Verification

```sh
server/.venv/bin/pytest server/tests/test_editor.py server/tests/test_tui.py -q
BOPBOP_LIVE_EDITOR_TEST=1 server/.venv/bin/pytest \
  server/tests/test_editor.py server/tests/test_tui.py -q -s
```

Tests use actual headless and interactive Neovim, sockets, managed-terminal PTYs and fixture
Ollama replies. Launcher regressions run through `bin/bopbop`, including a
service env file and a tmux server whose PATH differs from the caller's. CI
installs Neovim 0.11.5 and tmux so those tests have real editor dependencies.
The opt-in local-model test explains a function, proposes a
change, applies it, executes the generated Python, undoes it and confirms the
original disk file was never saved. The interactive tmux test drives Visual
selection, literal default leader keys, the question prompt, every F2–F9 key,
focus round trips, review apply and undo. Enable `BOPBOP_LIVE_EDITOR_TEST=1`
to run that same keystroke sequence against GPT OSS 20B. Screens and timings
stay in pytest's temporary directory.

API references: [Neovim API](https://neovim.io/doc/user/api/),
[region functions](https://neovim.io/doc/user/vimfn/#getregion()), and
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

Automatic suggestions while typing remain ticket 4's research topic.
