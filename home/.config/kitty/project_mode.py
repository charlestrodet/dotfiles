"""Select the Kitty project profile once per process."""

import fcntl
import os
import sys
from pathlib import Path


def project_mode():
    role = getattr(sys, "_kitty_tmux_launch_role", None)
    if role is not None:
        return role == "project"

    state_dir = Path(os.environ.get("KITTY_TMUX_STATE_DIR", Path.home() / ".local/state/kitty-tmux"))
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(state_dir / "kitty-projects.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
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
    print("include kitty-projects.conf")
