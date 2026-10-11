if status is-interactive
    # One persistent tmux session for each Kitty tab; skip nested and other terminals.
    if set -q KITTY_PID; and set -q KITTY_WINDOW_ID; and not set -q TMUX; and test "$TERM" = xterm-kitty; and command -q tmux
        tmux new-session -A -s "kitty-$KITTY_PID-$KITTY_WINDOW_ID"
    end
end
