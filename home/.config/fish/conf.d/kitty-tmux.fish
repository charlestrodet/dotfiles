# Track only commands entered in Kitty's project panes.
if status is-interactive; and set -q KITTY_TMUX_PROJECTS TMUX TMUX_PANE
    function __kitty_tmux_mark_fish_exit --on-event fish_exit
        command python3 "$HOME/.config/kitty/project_state.py" mark-close fish "$TMUX_PANE" "$fish_pid" >/dev/null 2>&1
    end

    function __kitty_tmux_preexec --on-event fish_preexec
        set -l command ''
        if test (string length -- "$argv[1]") -le 65536
            set command "$argv[1]"
        end
        command tmux set-option -p -t "$TMUX_PANE" @kitty_tmux_command "$command" >/dev/null 2>&1
    end

    function __kitty_tmux_postexec --on-event fish_postexec
        # tmux shutdown interrupts foreground jobs with HUP or TERM.
        if contains -- $status 129 143
            return
        end
        set -g __kitty_tmux_command_finished 1
    end

    function __kitty_tmux_posterror --on-event fish_posterror
        set -g __kitty_tmux_command_finished 1
    end

    function __kitty_tmux_clear_finished --on-event fish_prompt
        if set -q __kitty_tmux_command_finished
            command tmux set-option -p -t "$TMUX_PANE" @kitty_tmux_command '' >/dev/null 2>&1
            set -e __kitty_tmux_command_finished
        end
    end

    if not set -q KITTY_TMUX_RESTORE_COMMAND
        command tmux set-option -p -t "$TMUX_PANE" @kitty_tmux_command '' >/dev/null 2>&1
    end
end

# Put an interrupted command back in the editor once, without running it.
if set -q KITTY_TMUX_RESTORE_COMMAND
    set -g __kitty_tmux_restore_command "$KITTY_TMUX_RESTORE_COMMAND"
    set -e KITTY_TMUX_RESTORE_COMMAND
    if set -q TMUX_PANE
        command tmux set-option -p -t "$TMUX_PANE" @kitty_tmux_command "$__kitty_tmux_restore_command" >/dev/null 2>&1
    end

    function __kitty_tmux_restore_input --on-event fish_prompt
        commandline --replace -- "$__kitty_tmux_restore_command"
        set -e __kitty_tmux_restore_command
        functions -e __kitty_tmux_restore_input
    end
end
