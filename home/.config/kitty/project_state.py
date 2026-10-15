#!/usr/bin/env python3
"""Local state for named Kitty tabs and their tmux sessions."""

import argparse
import fcntl
import json
import os
import pwd
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


STATE_DIR = Path(os.environ.get("KITTY_TMUX_STATE_DIR", Path.home() / ".local/state/kitty-tmux"))
PROJECTS_FILE = STATE_DIR / "projects.json"
STARTUP_FILE = STATE_DIR / "startup.kitty-session"
SCRIPT = Path(__file__).resolve()
MAX_HISTORY_BYTES = 2 * 1024 * 1024
# tmux 3.5 escapes ASCII unit separators as literal \037; tabs survive intact.
SEP = "\t"


def ensure_state_dir():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)


def atomic_write(path, data):
    ensure_state_dir()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_lock(name="state", blocking=True):
    ensure_state_dir()
    with open(STATE_DIR / f".{name}.lock", "a+b") as lock:
        os.fchmod(lock.fileno(), 0o600)
        mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(lock, mode)
        yield


def read_state():
    if not PROJECTS_FILE.exists():
        return {"version": 1, "projects": [], "bindings": {}}
    data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError(f"Unsupported project state: {PROJECTS_FILE}")
    data.setdefault("bindings", {})
    return data


def startup_text(projects):
    lines = ["# Generated locally; do not add this file to dotfiles."]
    for project in projects:
        title = project["title"].replace("\r", " ").replace("\n", " ").replace("$", "$$")
        lines.extend((f"new_tab {title}", f"launch --env KITTY_PROJECT_ID={project['id']}"))
    return "\n".join(lines) + "\n"


def write_state(data):
    atomic_write(PROJECTS_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    atomic_write(STARTUP_FILE, startup_text(data["projects"]))


def initialize():
    with state_lock():
        data = read_state()
        write_state(data)


def session_name(project_id):
    return f"kitty-project-{project_id}"


def binding_key(kitty_pid, window_id):
    try:
        process_stat = Path(f"/proc/{kitty_pid}/stat").read_text()
        start_time = process_stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        start_time = "unknown"
    return f"{kitty_pid}:{start_time}:{window_id}"


def project_for_window(kitty_pid, window_id):
    return read_state()["bindings"].get(binding_key(kitty_pid, window_id))


def register(title, root, kitty_pid, window_id, previous_id=None, startup_id=None):
    """Return (project id, tmux session to rename), or (None, None)."""
    if not title.strip():
        return None, None
    with state_lock():
        data = read_state()
        key = binding_key(kitty_pid, window_id)
        project_id = previous_id or startup_id or data["bindings"].get(key)
        project = next((p for p in data["projects"] if p["id"] == project_id), None)
        if startup_id and project is None and not previous_id:
            return None, None  # A stale startup file must not recreate a closed project.
        new_project = project is None
        if new_project:
            project_id = previous_id or uuid.uuid4().hex
            project = {"id": project_id, "title": title, "root": root or str(Path.home())}
            data["projects"].append(project)
        else:
            project["title"] = title
            if not project.get("root"):
                project["root"] = root or str(Path.home())
        data["bindings"][key] = project_id
        write_state(data)
    old_session = f"kitty-{kitty_pid}-{window_id}" if new_project and not previous_id else None
    return project_id, old_session


def forget(project_id, kitty_pid=None, window_id=None):
    if not project_id:
        return
    with state_lock():
        data = read_state()
        data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
        if kitty_pid is not None and window_id is not None:
            data["bindings"].pop(binding_key(kitty_pid, window_id), None)
        write_state(data)


def delete_snapshot(project_id):
    # Wait for an in-flight capture after the project has left the active list.
    with state_lock("snapshot"):
        for suffix in (".json", ".recovery.json"):
            (STATE_DIR / f"{project_id}{suffix}").unlink(missing_ok=True)


def clear_binding(kitty_pid, window_id):
    with state_lock():
        data = read_state()
        if data["bindings"].pop(binding_key(kitty_pid, window_id), None) is not None:
            write_state(data)


def reorder(project_ids):
    with state_lock():
        data = read_state()
        by_id = {p["id"]: p for p in data["projects"]}
        ordered = [by_id[project_id] for project_id in project_ids if project_id in by_id]
        ordered_ids = {p["id"] for p in ordered}
        ordered.extend(p for p in data["projects"] if p["id"] not in ordered_ids)
        if ordered != data["projects"]:
            data["projects"] = ordered
            write_state(data)


def tmux(*args, check=True):
    return subprocess.run(
        ("tmux", *map(str, args)), capture_output=True, text=True, check=check,
    )


def tmux_has_session(name):
    return tmux("has-session", "-t", f"={name}", check=False).returncode == 0


def pane_history(pane_id):
    result = tmux("capture-pane", "-p", "-S", "-", "-t", pane_id)
    raw = result.stdout.encode("utf-8", errors="replace")
    if len(raw) > MAX_HISTORY_BYTES:
        raw = raw[-MAX_HISTORY_BYTES:]
        raw = raw.partition(b"\n")[2]
    return raw.decode("utf-8", errors="replace")


def snapshot_project(project):
    name = session_name(project["id"])
    if not tmux_has_session(name):
        return False
    window_rows = tmux("list-windows", "-t", f"={name}", "-F",
                       SEP.join(("#{window_id}", "#{window_index}", "#{window_name}",
                                 "#{window_layout}", "#{window_active}",
                                 "#{window_zoomed_flag}"))).stdout.splitlines()
    pane_rows = tmux("list-panes", "-s", "-t", f"={name}", "-F",
                     SEP.join(("#{window_id}", "#{pane_id}", "#{pane_index}",
                               "#{pane_current_path}", "#{pane_active}"))).stdout.splitlines()
    panes_by_window = {}
    for row in pane_rows:
        window_id, pane_id, pane_index, *path_and_active = row.split(SEP)
        cwd, active = SEP.join(path_and_active[:-1]), path_and_active[-1]
        panes_by_window.setdefault(window_id, []).append(
            {"index": int(pane_index), "cwd": cwd, "active": active == "1",
             "history": pane_history(pane_id)})
    windows = []
    for row in window_rows:
        window_id, index, *title_and_layout = row.split(SEP)
        title = SEP.join(title_and_layout[:-3])
        layout, active, zoomed = title_and_layout[-3:]
        windows.append({"index": int(index), "title": title, "layout": layout,
                        "active": active == "1", "zoomed": zoomed == "1",
                        "panes": panes_by_window.get(window_id, [])})
    if not windows:
        return False
    snapshot = {"version": 1, "windows": windows}
    atomic_write(STATE_DIR / f"{project['id']}.json", json.dumps(snapshot, ensure_ascii=False) + "\n")
    return True


def snapshot_all(blocking=True):
    try:
        with state_lock("snapshot", blocking=blocking):
            for project in read_state()["projects"]:
                try:
                    snapshot_project(project)
                except (OSError, subprocess.CalledProcessError, ValueError) as error:
                    print(f"Could not save {project['title']}: {error}", file=sys.stderr)
    except BlockingIOError:
        pass


def pane_command(project_id, window_number, pane_number):
    return (sys.executable, str(SCRIPT), "pane-shell", project_id,
            str(window_number), str(pane_number))


def existing_directory(path, fallback):
    if path and Path(path).is_dir():
        return path
    if fallback and Path(fallback).is_dir():
        return fallback
    return str(Path.home())


def preserve_failed_snapshot(project_id, original):
    recovery = STATE_DIR / f"{project_id}.recovery.json"
    if not recovery.exists():
        atomic_write(recovery, original)


def restore_project(project):
    project_id = project["id"]
    name = session_name(project_id)
    if tmux_has_session(name):
        return
    snapshot_file = STATE_DIR / f"{project_id}.json"
    if not snapshot_file.exists():
        return
    original = snapshot_file.read_text(encoding="utf-8")
    try:
        snapshot = json.loads(original)
    except ValueError:
        preserve_failed_snapshot(project_id, original)
        raise
    windows = snapshot.get("windows", []) if snapshot.get("version") == 1 else []
    if not windows:
        return
    created = False
    restored_windows = {}
    incomplete = False
    for window_number, window in enumerate(windows):
        try:
            panes = window["panes"]
            if not panes:
                incomplete = True
                continue
            first = panes[0]
            cwd = existing_directory(first.get("cwd"), project.get("root"))
            command = pane_command(project_id, window_number, 0)
            before = set(tmux("list-windows", "-t", f"={name}", "-F", "#{window_id}").stdout.splitlines()) if created else set()
            if not created:
                tmux("new-session", "-d", "-s", name, "-x", "240", "-y", "80",
                     "-n", window["title"], "-c", cwd, *command)
                created = True
            else:
                tmux("new-window", "-d", "-t", f"{name}:{window['index']}", "-n", window["title"],
                     "-c", cwd, *command)
            after = set(tmux("list-windows", "-t", f"={name}", "-F", "#{window_id}").stdout.splitlines())
            window_id = next(iter(after - before))
            restored_windows[window["index"]] = window_id
            pane_ids = [tmux("list-panes", "-t", window_id, "-F", "#{pane_id}").stdout.splitlines()[0]]
            complete = True
            for pane_number, pane in enumerate(panes[1:], start=1):
                cwd = existing_directory(pane.get("cwd"), project.get("root"))
                before_panes = set(tmux("list-panes", "-t", window_id, "-F", "#{pane_id}").stdout.splitlines())
                result = tmux("split-window", "-d", "-h", "-t", window_id, "-c", cwd,
                              *pane_command(project_id, window_number, pane_number), check=False)
                if result.returncode:
                    print(f"Could not restore pane {pane_number + 1} in {window['title']}: "
                          f"{result.stderr.strip()}", file=sys.stderr)
                    complete = False
                    incomplete = True
                    break
                after_panes = set(tmux("list-panes", "-t", window_id, "-F", "#{pane_id}").stdout.splitlines())
                pane_ids.append(next(iter(after_panes - before_panes)))
                tmux("select-layout", "-t", window_id, "tiled", check=False)
            if complete and window.get("layout"):
                result = tmux("select-layout", "-t", window_id, window["layout"], check=False)
                if result.returncode:
                    print(f"Could not restore layout for {window['title']}; using tiled layout: "
                          f"{result.stderr.strip()}", file=sys.stderr)
                    tmux("select-layout", "-t", window_id, "tiled", check=False)
                    incomplete = True
            elif not complete:
                tmux("select-layout", "-t", window_id, "tiled", check=False)
            active_pane_number = next((n for n, p in enumerate(panes) if p.get("active")), 0)
            active_pane_id = pane_ids[min(active_pane_number, len(pane_ids) - 1)]
            tmux("select-pane", "-t", active_pane_id, check=False)
            if window.get("zoomed"):
                tmux("resize-pane", "-Z", "-t", active_pane_id, check=False)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError,
                IndexError, StopIteration, TypeError) as error:
            incomplete = True
            print(f"Could not fully restore window {window_number + 1} in "
                  f"{project['title']}: {error}", file=sys.stderr)
    if incomplete:
        preserve_failed_snapshot(project_id, original)
    active_window = next((restored_windows.get(w.get("index")) for w in windows
                          if isinstance(w, dict) and w.get("active") and
                          w.get("index") in restored_windows), None)
    if active_window is None:
        active_window = next(iter(restored_windows.values()), None)
    if active_window:
        tmux("select-window", "-t", active_window, check=False)


def attach_auto():
    if os.environ.get("TMUX"):
        exec_machine_shell()
    kitty_pid = os.environ["KITTY_PID"]
    window_id = os.environ["KITTY_WINDOW_ID"]
    data = read_state()
    project_id = os.environ.get("KITTY_PROJECT_ID") or data["bindings"].get(binding_key(kitty_pid, window_id))
    project = next((p for p in data["projects"] if p["id"] == project_id), None)
    if project is None:
        os.execvp("tmux", ("tmux", "new-session", "-A", "-s", f"kitty-{kitty_pid}-{window_id}"))
    name = session_name(project_id)
    with state_lock(f"restore-{project_id}"):
        if not tmux_has_session(name):
            try:
                # Keep periodic captures from replacing saved panes mid-restore.
                with state_lock("snapshot"):
                    restore_project(project)
            except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
                print(f"Could not restore {project['title']}: {error}", file=sys.stderr)
    os.execvp("tmux", ("tmux", "new-session", "-A", "-s", name,
                       "-c", existing_directory(project.get("root"), None)))


def pane_shell(project_id, window_number, pane_number):
    try:
        snapshot = json.loads((STATE_DIR / f"{project_id}.json").read_text(encoding="utf-8"))
        history = snapshot["windows"][window_number]["panes"][pane_number].get("history", "")
        lines = history.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            # capture-pane includes unused rows below the old prompt.
            sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
    except (OSError, ValueError, KeyError, IndexError):
        pass
    exec_machine_shell()


def exec_machine_shell():
    shell = os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    os.execvp(shell, (shell,))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("attach-auto")
    sub.add_parser("snapshot-all")
    one = sub.add_parser("snapshot-one")
    one.add_argument("project_id")
    cleanup = sub.add_parser("delete-snapshot")
    cleanup.add_argument("project_id")
    pane = sub.add_parser("pane-shell")
    pane.add_argument("project_id")
    pane.add_argument("window_number", type=int)
    pane.add_argument("pane_number", type=int)
    args = parser.parse_args()
    if args.command == "init":
        initialize()
    elif args.command == "attach-auto":
        attach_auto()
    elif args.command == "snapshot-all":
        snapshot_all(blocking=False)
    elif args.command == "snapshot-one":
        with state_lock("snapshot"):
            project = next((p for p in read_state()["projects"] if p["id"] == args.project_id), None)
            if project:
                snapshot_project(project)
    elif args.command == "delete-snapshot":
        delete_snapshot(args.project_id)
    elif args.command == "pane-shell":
        pane_shell(args.project_id, args.window_number, args.pane_number)


if __name__ == "__main__":
    main()
