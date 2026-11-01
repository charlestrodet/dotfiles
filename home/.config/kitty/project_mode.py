"""Select the Kitty project profile once per process."""

import fcntl
import os
import shlex
import sys
from pathlib import Path


STATE_DIR = Path(os.environ.get("KITTY_TMUX_STATE_DIR", Path.home() / ".local/state/kitty-tmux"))
CONFIG_DIR = Path(__file__).resolve().parent
os.environ.pop("TMUX", None)
os.environ.pop("TMUX_PANE", None)


def project_mode():
    role = getattr(sys, "_kitty_tmux_launch_role", None)
    if role is not None:
        return role == "project"

    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE_DIR / "kitty-projects.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        role = "plain"
    except OSError:
        os.close(fd)
        raise
    else:
        # Keep the descriptor in Kitty's Python process; the kernel frees it on exit.
        sys._kitty_tmux_project_lock_fd = fd
        role = "project"

    sys._kitty_tmux_launch_role = role
    return role == "project"


if project_mode():
    sys.path.insert(0, str(CONFIG_DIR))
    import project_state
    project_state.initialize()  # Repair a stale startup file before Kitty reads it.
    print("include kitty-projects.conf")
    print(f"shell python3 {shlex.quote(str(CONFIG_DIR / 'project_state.py'))} attach-auto")
    print(f"startup_session {shlex.quote(str(STATE_DIR / 'startup.kitty-session'))}")
    print(f"map kitty_mod+alt+t kitten {shlex.quote(str(CONFIG_DIR / 'project_title.py'))}")
