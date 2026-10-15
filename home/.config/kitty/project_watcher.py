"""Observe named Kitty tabs without changing Kitty's normal close actions."""

import os
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_state as state  # noqa: E402


_tabs = {}
_closed_os_windows = set()
_orders = {}


def snapshot_in_background(command, *args):
    subprocess.Popen(
        ("python3", str(state.SCRIPT), command, *args),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def kill_session_in_background(name):
    subprocess.Popen(
        ("tmux", "kill-session", "-t", f"={name}"),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def active_project_for_window(kitty_pid, window):
    data = state.read_state()
    project_id = (data["bindings"].get(state.binding_key(kitty_pid, window.id)) or
                  window.child.environ.get("KITTY_PROJECT_ID"))
    return project_id if any(p["id"] == project_id for p in data["projects"]) else None


def on_load(boss, data):
    state.initialize()


def pane_cwd(tab, source_session):
    if state.tmux_has_session(source_session):
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
        key = (manager.os_window_id, tab.id)
        previous = _tabs.get(key)
        title = tab.name.strip()
        if not title:
            project_id = previous["id"] if previous else state.project_for_window(kitty_pid, active.id)
            if project_id and (previous is None or previous["title"]):
                state.forget(project_id, kitty_pid, active.id)
                snapshot_in_background("delete-snapshot", project_id)
                source = state.session_name(project_id)
                temporary = f"kitty-{kitty_pid}-{active.id}"
                if state.tmux_has_session(source) and not state.tmux_has_session(temporary):
                    state.tmux("rename-session", "-t", f"={source}", temporary)
                _tabs[key] = {"id": None, "title": "", "window_id": active.id}
            continue
        if previous and previous["title"] == title:
            order.append(previous["id"])
            continue
        window_id = active.id
        source_session = f"kitty-{kitty_pid}-{window_id}"
        # A cleared title leaves the original child environment unchanged.
        startup_id = (active.child.environ.get("KITTY_PROJECT_ID")
                      if previous is None else None)
        project_id, old_session = state.register(
            title, pane_cwd(tab, source_session), kitty_pid, window_id,
            previous_id=previous["id"] if previous else None, startup_id=startup_id,
        )
        if not project_id:
            continue
        _tabs[key] = {"id": project_id, "title": title, "window_id": window_id}
        order.append(project_id)
        if old_session and state.tmux_has_session(old_session):
            state.tmux("rename-session", "-t", f"={old_session}", state.session_name(project_id))
            snapshot_in_background("snapshot-one", project_id)
    current_order = tuple(order)
    if order and _orders.get(manager.os_window_id) != current_order:
        state.reorder(order)
        _orders[manager.os_window_id] = current_order


def on_close(boss, window, data):
    kitty_pid = os.getpid()
    os_window_id = window.os_window_id
    if getattr(boss, "shutting_down", False) or os_window_id not in boss.os_window_map:
        if os_window_id not in _closed_os_windows:
            _closed_os_windows.add(os_window_id)
            snapshot_in_background("snapshot-all")
        _orders.pop(os_window_id, None)
        if active_project_for_window(kitty_pid, window):
            state.clear_binding(kitty_pid, window.id)
        else:
            kill_session_in_background(f"kitty-{kitty_pid}-{window.id}")
        return
    tab = window.tabref()
    if tab is None or len(tab.windows) != 1:
        return
    known = _tabs.pop((os_window_id, tab.id), None)
    project_id = (known["id"] if known else
                  active_project_for_window(kitty_pid, window))
    if project_id:
        state.forget(project_id, kitty_pid, window.id)
        snapshot_in_background("delete-snapshot", project_id)
        kill_session_in_background(state.session_name(project_id))
    else:
        kill_session_in_background(f"kitty-{kitty_pid}-{window.id}")
