if status is-interactive
    # Only project-mode Kitty tabs own tmux sessions.
    if set -q KITTY_TMUX_PROJECTS; and test "$KITTY_TMUX_PROJECTS" = 1; and set -q KITTY_PID; and set -q KITTY_WINDOW_ID; and not set -q TMUX; and test "$TERM" = xterm-kitty; and command -q tmux; and command -q python3
        exec python3 "$HOME/.config/kitty/project_state.py" attach-auto
    end
end
