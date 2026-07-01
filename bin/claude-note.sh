#!/usr/bin/env bash
# claude-note.sh — drop a note into a live (or resumed) Claude Code session.
#
# Usage:
#   claude-note.sh <target> <note text...>
#
# <target> is a tmux session name (e.g. a project name) or a project dir
# basename under ~/code/. Resolution order:
#
#   1. Live tmux pane running claude (matched by session name, then by
#      project dir basename) → note is typed into the pane.
#        - Claude busy    → lands as a queued message, read when the
#          current step yields (same as typing mid-task at the keyboard)
#        - Claude idle    → submits immediately, wakes the agent
#        - Claude at a prompt (AskUserQuestion / permission dialog) → NOT
#          delivered (a note would be read as a menu choice); exit 3.
#   2. No live pane → resume the project's most recent Claude session in
#      a NEW detached tmux session named <target>, wait for the prompt,
#      then type the note. Attach later with: tmux attach -t <target>
#
# Notes are prefixed "[note from <FROM> via <VIA>]" so the receiving Claude
# knows the provenance. Configure via env:
#   CLAUDE_NOTE_FROM   provenance author  (default: "the owner")
#   CLAUDE_NOTE_VIA    provenance channel (default: "claude-note")
# Multi-line notes are safe (bracketed paste).
#
# Exit codes:
#   0 delivered · 1 error · 2 ambiguous target · 3 blocked (session at a prompt)

set -euo pipefail

target="${1:?usage: claude-note.sh <target> <note text...>}"
shift
note="${*:?usage: claude-note.sh <target> <note text...>}"
note="[note from ${CLAUDE_NOTE_FROM:-the owner} via ${CLAUDE_NOTE_VIA:-claude-note}] $note"

CODE_DIR="$HOME/code"
RESUME_TIMEOUT=45

# Prefer the linuxbrew tmux build if present (its server speaks a newer
# protocol than some distro tmux 3.4 builds, which silently drop clients).
# Services often don't have linuxbrew on PATH, so pin it explicitly.
TMUX_BIN=$(command -v tmux || true)
[[ -x /home/linuxbrew/.linuxbrew/bin/tmux ]] && TMUX_BIN=/home/linuxbrew/.linuxbrew/bin/tmux  # leak-allow: standard optional linuxbrew path
tmux() { "$TMUX_BIN" "$@"; }

pane_state() {
    local screen
    screen=$(tmux capture-pane -p -t "$1" 2>/dev/null || true)
    # A modal selection (AskUserQuestion menu or a permission dialog): here
    # Enter means "choose an option", not "send a message" — typing a note
    # would be swallowed or pick an option. Refuse to inject.
    if grep -qE "Enter to select|Do you want to proceed|Would you like to proceed" <<<"$screen"; then
        echo prompt
    elif grep -q "esc to interrupt" <<<"$screen"; then
        echo busy
    else
        echo idle
    fi
}

inject() { # inject <pane> — type the note + Enter via bracketed paste
    local pane="$1"
    tmux set-buffer -b claude-note -- "$note"
    tmux paste-buffer -d -p -b claude-note -t "$pane"
    sleep 0.4
    tmux send-keys -t "$pane" Enter
}

peek() { # last few non-blank pane lines, for the report-back
    tmux capture-pane -p -t "$1" | grep -v '^[[:space:]]*$' | tail -n 6
}

# --- 1. find a live claude pane for the target ---------------------------
candidates=()
if tmux list-sessions &>/dev/null; then
    while IFS='|' read -r pane sess cmd_name path; do
        [[ "$cmd_name" == "claude" ]] || continue
        if [[ "$sess" == "$target" || "$(basename "$path")" == "$target" ]]; then
            candidates+=("$pane|$sess|$path")
        fi
    done < <(tmux list-panes -a -F "#{session_name}:#{window_index}.#{pane_index}|#{session_name}|#{pane_current_command}|#{pane_current_path}")
fi

if (( ${#candidates[@]} > 1 )); then
    # Same project open in several sessions — exact session-name match wins,
    # otherwise refuse and list so the caller can re-ask with a session name.
    exact=()
    for c in "${candidates[@]}"; do
        IFS='|' read -r _ sess _ <<<"$c"
        [[ "$sess" == "$target" ]] && exact+=("$c")
    done
    if (( ${#exact[@]} == 1 )); then
        candidates=("${exact[@]}")
    else
        echo "ambiguous target '$target' — matching sessions:" >&2
        for c in "${candidates[@]}"; do
            IFS='|' read -r pane sess path <<<"$c"
            echo "  $sess ($path) [$(pane_state "$pane")]" >&2
        done
        exit 2
    fi
fi

if (( ${#candidates[@]} == 1 )); then
    IFS='|' read -r pane sess path <<<"${candidates[0]}"
    state=$(pane_state "$pane")
    if [[ "$state" == prompt ]]; then
        echo "BLOCKED on tmux session '$sess' ($path) — claude is waiting at an interactive prompt/menu; a note would be read as a menu selection, so it was NOT delivered. Answer the prompt (tmux attach -t $sess), then retry." >&2
        echo "--- pane tail ---" >&2
        peek "$pane" >&2
        exit 3
    fi
    inject "$pane"
    if [[ "$state" == busy ]]; then
        echo "QUEUED on tmux session '$sess' ($path) — claude is mid-task, will see the note when the current step yields."
    else
        echo "SUBMITTED to tmux session '$sess' ($path) — claude was idle, now working on the note."
    fi
    echo "--- pane tail ---"
    peek "$pane"
    exit 0
fi

# --- 2. no live pane: resume most recent session in a new tmux session ----
proj_dir="$CODE_DIR/$target"
[[ -d "$proj_dir" ]] || { echo "no live claude session named '$target' and no project at $proj_dir" >&2; exit 1; }

munged=$(printf '%s' "$proj_dir" | sed 's/[^[:alnum:]]/-/g')
transcripts="$HOME/.claude/projects/$munged"
latest=$(ls -t "$transcripts"/*.jsonl 2>/dev/null | head -1) || true
[[ -n "${latest:-}" ]] || { echo "no prior claude sessions found for $proj_dir (nothing to resume)" >&2; exit 1; }
session_id=$(basename "$latest" .jsonl)

# pick a free tmux session name
sess="$target"
n=2
while tmux has-session -t "=$sess" &>/dev/null; do sess="$target-$((n++))"; done

tmux new-session -d -s "$sess" -c "$proj_dir" \
    "claude --resume $session_id --dangerously-skip-permissions"

# wait for the TUI prompt before typing (auto-accept the folder-trust
# dialog if it appears — these are the owner's own projects)
deadline=$(( $(date +%s) + RESUME_TIMEOUT ))
pane="$sess:0.0"
while :; do
    screen=$(tmux capture-pane -p -t "$pane" 2>/dev/null || true)
    if grep -q "Yes, I trust this folder" <<<"$screen"; then
        tmux send-keys -t "$pane" Enter
        sleep 2
        continue
    fi
    grep -qE "bypass permissions|shift\+tab to cycle" <<<"$screen" && break
    if (( $(date +%s) > deadline )); then
        echo "resumed session '$sess' but the claude prompt never appeared within ${RESUME_TIMEOUT}s — note NOT delivered. Check: tmux attach -t $sess" >&2
        exit 1
    fi
    sleep 1
done
sleep 1  # let the TUI settle

inject "$pane"
echo "WOKE UP project '$target': resumed session ${session_id:0:8}… in new tmux session '$sess' and submitted the note. Attach later with: tmux attach -t $sess"
echo "--- pane tail ---"
peek "$pane"
