"""Observe named Kitty tabs without changing Kitty's normal close actions."""

import os
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_state as state  # noqa: E402


_closed_os_windows = set()
_orders = {}


def background(*command):
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def snapshot_in_background(command, *args):
    background("python3", str(state.SCRIPT), command, *args)


def kill_session_in_background(name):
    background("tmux", "kill-session", "-t", f"={name}")


def active_project_for_window(window):
    data = state.read_state()
    tab = window.tabref()
    project_id = ((getattr(tab, "_kitty_tmux_id", None) if tab else None) or
                  getattr(window.child, "env", {}).get("KITTY_PROJECT_ID") or
                  window.child.environ.get("KITTY_PROJECT_ID"))
    return project_id if any(p["id"] == project_id for p in data["projects"]) else None


def on_load(boss, data):
    if getattr(boss, "_kitty_tmux_close_tracking", False):
        return
    boss._kitty_tmux_close_requested = set()
    original = boss.mark_window_for_close

    def mark_window_for_close(q=None):
        window = boss.window_id_map.get(q) if isinstance(q, int) else (q or boss.active_window)
        if window is not None:
            boss._kitty_tmux_close_requested.add(window.id)
        try:
            return original(q)
        except Exception:
            if window is not None:
                boss._kitty_tmux_close_requested.discard(window.id)
            raise

    boss.mark_window_for_close = mark_window_for_close
    boss._kitty_tmux_close_tracking = True


def forget_closed_project(project_id, reason):
    state.forget(project_id, reason=reason)
    snapshot_in_background("delete-snapshot", project_id)
    kill_session_in_background(state.session_name(project_id))


def pane_cwd(tab, source_session):
    result = state.tmux("display-message", "-p", "-t", f"={source_session}",
                        "#{pane_current_path}", check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return tab.get_cwd_of_active_window() or str(Path.home())


def on_tab_bar_dirty(boss, window, data):
    reconcile_tabs(data["tab_manager"])


def reconcile_tabs(manager):
    kitty_pid = os.getpid()
    order = []
    for tab in tuple(manager.tabs):
        active = tab.active_window
        if active is None:
            continue
        previous_id = getattr(tab, "_kitty_tmux_id", None)
        previous_title = getattr(tab, "_kitty_tmux_title", None)
        title = tab.name.strip()
        if not title:
            startup_id = (getattr(active.child, "env", {}).get("KITTY_PROJECT_ID") or
                          active.child.environ.get("KITTY_PROJECT_ID"))
            project_id = previous_id or (startup_id if previous_title is None else None)
            if project_id and previous_title != "":
                state.forget(project_id, reason="title-cleared")
                snapshot_in_background("delete-snapshot", project_id)
                source = state.session_name(project_id)
                temporary = f"kitty-{kitty_pid}-{active.id}"
                if not state.tmux_has_session(temporary):
                    state.tmux("rename-session", "-t", f"={source}", temporary, check=False)
                tab._kitty_tmux_id = None
                tab._kitty_tmux_title = ""
            continue
        if previous_id and previous_title == title:
            order.append(previous_id)
            continue
        window_id = active.id
        source_session = f"kitty-{kitty_pid}-{window_id}"
        # A cleared title leaves the original child environment unchanged.
        startup_id = ((getattr(active.child, "env", {}).get("KITTY_PROJECT_ID") or
                       active.child.environ.get("KITTY_PROJECT_ID"))
                      if previous_title is None else None)
        project_id, rename_temporary = state.register(
            title, pane_cwd(tab, source_session),
            previous_id=previous_id, startup_id=startup_id,
        )
        if not project_id:
            continue
        tab._kitty_tmux_id = project_id
        tab._kitty_tmux_title = title
        order.append(project_id)
        if rename_temporary:
            result = state.tmux("rename-session", "-t", f"={source_session}",
                                state.session_name(project_id), check=False)
            if result.returncode == 0:
                snapshot_in_background("snapshot-one", project_id)
    current_order = tuple(order)
    if order and _orders.get(manager.os_window_id) != current_order:
        state.reorder(order)
        _orders[manager.os_window_id] = current_order


def on_close(boss, window, data):
    kitty_pid = os.getpid()
    os_window_id = window.os_window_id
    tab = window.tabref()
    last_window = tab is not None and len(tab.windows) == 1
    project_id = active_project_for_window(window)
    if last_window:
        tab._kitty_tmux_id = None
        tab._kitty_tmux_title = None
    explicit_close = window.id in getattr(boss, "_kitty_tmux_close_requested", ())
    if explicit_close:
        boss._kitty_tmux_close_requested.discard(window.id)
    shutting_down = getattr(boss, "shutting_down", False)
    if explicit_close and last_window and not shutting_down and project_id:
        forget_closed_project(project_id, "kitty-tab-close")
        return
    if shutting_down or os_window_id not in boss.os_window_map:
        if os_window_id not in _closed_os_windows:
            _closed_os_windows.add(os_window_id)
            snapshot_in_background("snapshot-all", "--wait")
        _orders.pop(os_window_id, None)
        if not project_id:
            kill_session_in_background(f"kitty-{kitty_pid}-{window.id}")
        return
    if not last_window:
        return
    if project_id:
        if state.consume_close_intent(project_id):
            forget_closed_project(project_id, "tmux-last-window-close")
        else:
            # A lost tmux client or server is not proof of an intentional close.
            snapshot_in_background("snapshot-all", "--wait")
    else:
        kill_session_in_background(f"kitty-{kitty_pid}-{window.id}")
