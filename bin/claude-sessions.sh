#!/usr/bin/env bash
# claude-sessions.sh — discover live Claude Code sessions on this machine.
#
# Usage:
#   claude-sessions.sh              # list all sessions (tmux + non-tmux)
#   claude-sessions.sh peek <name>  # tail the pane of a tmux session (last 30 lines)
#
# List output: one line per session, tab-separated:
#   NAME  STATE  PROJECT  DETAIL
#   STATE: busy | idle | prompt | untmuxed
#     prompt   = waiting at an AskUserQuestion menu / permission dialog
#     untmuxed = visible but not injectable (TIOCSTI disabled)
#
# Companion: claude-note.sh injects a note into a session found here.

set -euo pipefail

# Prefer the linuxbrew tmux build if present (newer server protocol than some
# distro tmux 3.4 builds). Services often lack linuxbrew on PATH, so pin it.
TMUX_BIN=$(command -v tmux || true)
[[ -x /home/linuxbrew/.linuxbrew/bin/tmux ]] && TMUX_BIN=/home/linuxbrew/.linuxbrew/bin/tmux  # leak-allow: standard optional linuxbrew path
tmux() { "$TMUX_BIN" "$@"; }

pane_state() {
    local screen
    screen=$(tmux capture-pane -p -t "$1" 2>/dev/null || true)
    if grep -qE "Enter to select|Do you want to proceed|Would you like to proceed" <<<"$screen"; then
        echo prompt
    elif grep -q "esc to interrupt" <<<"$screen"; then
        echo busy
    else
        echo idle
    fi
}

cmd="${1:-list}"

if [[ "$cmd" == "peek" ]]; then
    name="${2:?usage: claude-sessions.sh peek <tmux-session>}"
    pane=$(tmux list-panes -a -F "#{session_name}:#{window_index}.#{pane_index} #{session_name} #{pane_current_command}" 2>/dev/null \
        | awk -v n="$name" '$2 == n && $3 == "claude" {print $1; exit}')
    [[ -n "$pane" ]] || { echo "no claude pane in tmux session '$name'" >&2; exit 1; }
    echo "[$pane — $(pane_state "$pane")]"
    tmux capture-pane -p -t "$pane" | grep -v '^[[:space:]]*$' | tail -n 30
    exit 0
fi

# --- list ---
tmux_ttys=""
if tmux list-sessions &>/dev/null; then
    while IFS='|' read -r pane tty cmd_name path; do
        [[ "$cmd_name" == "claude" ]] || continue
        tmux_ttys+="$tty "
        printf '%s\t%s\t%s\ttmux:%s\n' "${pane%%:*}" "$(pane_state "$pane")" "$path" "$pane"
    done < <(tmux list-panes -a -F "#{session_name}:#{window_index}.#{pane_index}|#{pane_tty}|#{pane_current_command}|#{pane_current_path}")
fi

# Non-tmux interactive claude processes (have a pts not owned by tmux).
# These are listed for visibility but cannot be injected into (kernel
# dev.tty.legacy_tiocsti=0 blocks typing into a terminal we don't own).
while read -r pid tty args; do
    [[ "$tty" == pts/* ]] || continue
    [[ "$tmux_ttys" == *"/dev/$tty "* ]] && continue
    [[ "$args" == *" -p "* || "$args" == *"--print"* ]] && continue  # headless turns
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "?")
    printf '%s\t%s\t%s\tpid:%s (not injectable — not in tmux)\n' "$(basename "$cwd")" untmuxed "$cwd" "$pid"
done < <(ps -eo pid=,tty=,args= | awk '$3 == "claude" || $3 ~ /\/claude$/')
