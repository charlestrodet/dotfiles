#!/usr/bin/env python3
"""Local state for named Kitty tabs and their tmux sessions."""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


STATE_DIR = Path(os.environ.get("KITTY_TMUX_STATE_DIR", Path.home() / ".local/state/kitty-tmux"))
PROJECTS_FILE = STATE_DIR / "projects.json"
STARTUP_FILE = STATE_DIR / "startup.kitty-session"
SCRIPT = Path(__file__).resolve()
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_COMMAND_BYTES = 64 * 1024
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
SGR = re.compile(r"\x1b\[[0-9;:]*m")
# tmux 3.5 escapes ASCII unit separators as literal \037; tabs survive intact.
SEP = "\t"


def ensure_state_dir():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)


def closed_dir():
    return STATE_DIR / "closed"


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
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
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
        return {"version": 1, "projects": []}
    data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError(f"Unsupported project state: {PROJECTS_FILE}")
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
        # A new project Kitty process has its own runtime tab map.
        data.pop("bindings", None)
        write_state(data)
        for path in STATE_DIR.glob(".command-*"):
            path.unlink()
        if closed_dir().exists():
            cutoff = time.time() - 30 * 24 * 60 * 60
            for path in closed_dir().iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()


def session_name(project_id):
    return f"kitty-project-{project_id}"


def close_intent_path(project_id):
    return STATE_DIR / f".close-intent-{project_id}.json"


def mark_close_intent(kind, pane_id, fish_pid=None):
    """Mark an intentional exit before tmux destroys the last window."""
    if kind not in ("fish", "pane", "window") or not re.fullmatch(r"%[0-9]+", pane_id):
        return False
    result = tmux("display-message", "-p", "-t", pane_id,
                  SEP.join(("#{session_name}", "#{pane_pid}",
                            "#{session_windows}", "#{window_panes}")),
                  check=False)
    fields = result.stdout.strip().split(SEP)
    if result.returncode or len(fields) != 4:
        return False
    name, pane_pid, windows, panes = fields
    if not name.startswith("kitty-project-"):
        return False
    project_id = name.removeprefix("kitty-project-")
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        return False
    if kind == "fish" and str(fish_pid) != pane_pid:
        return False  # An interactive fish nested inside the pane did not close it.
    if windows != "1" or (kind != "window" and panes != "1"):
        return False
    with state_lock():
        if not any(p["id"] == project_id for p in read_state()["projects"]):
            return False
        atomic_write(close_intent_path(project_id), json.dumps({
            "boot_id": BOOT_ID, "monotonic": time.monotonic(),
        }) + "\n")
    return True


def consume_close_intent(project_id):
    path = close_intent_path(project_id)
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    path.unlink(missing_ok=True)
    if not isinstance(marker, dict) or not isinstance(marker.get("monotonic"), (int, float)):
        return False
    age = time.monotonic() - marker["monotonic"]
    return marker.get("boot_id") == BOOT_ID and 0 <= age <= 30


def register(title, root, previous_id=None, startup_id=None):
    """Return (project id, rename temporary session), or (None, None)."""
    if not title.strip():
        return None, None
    with state_lock():
        data = read_state()
        project_id = previous_id or startup_id
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
        write_state(data)
    return project_id, new_project and not previous_id


def forget(project_id, reason="unspecified"):
    if not project_id:
        return
    with state_lock():
        data = read_state()
        project = next((p for p in data["projects"] if p["id"] == project_id), None)
        if project:
            closed_dir().mkdir(mode=0o700, exist_ok=True)
            atomic_write(closed_dir() / f"{project_id}.project.json",
                         json.dumps({**project, "_closed_reason": reason,
                                     "_closed_boot_id": BOOT_ID,
                                     "_closed_at": time.time()}, ensure_ascii=False) + "\n")
        data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
        write_state(data)
        close_intent_path(project_id).unlink(missing_ok=True)


def delete_snapshot(project_id):
    # Wait for an in-flight capture after the project has left the active list.
    with state_lock("snapshot"):
        for suffix in (".json", ".recovery.json"):
            path = STATE_DIR / f"{project_id}{suffix}"
            if path.exists():
                closed_dir().mkdir(mode=0o700, exist_ok=True)
                os.replace(path, closed_dir() / path.name)


def reorder(project_ids):
    with state_lock():
        data = read_state()
        by_id = {p["id"]: p for p in data["projects"]}
        seen = set()
        visible = []
        for project_id in project_ids:
            if project_id in by_id and project_id not in seen:
                visible.append(by_id[project_id])
                seen.add(project_id)
        next_visible = iter(visible)
        ordered = [next(next_visible) if p["id"] in seen else p for p in data["projects"]]
        if ordered != data["projects"]:
            data["projects"] = ordered
            write_state(data)


def tmux(*args, check=True):
    return subprocess.run(
        ("tmux", *map(str, args)), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def tmux_has_session(name):
    return tmux("has-session", "-t", f"={name}", check=False).returncode == 0


def pane_history(pane_id):
    result = tmux("capture-pane", "-e", "-p", "-S", "-", "-t", pane_id)
    raw = result.stdout.encode("utf-8", errors="replace")
    if len(raw) > MAX_HISTORY_BYTES:
        raw = raw[-MAX_HISTORY_BYTES:]
        raw = raw.partition(b"\n")[2]
    return raw.decode("utf-8", errors="replace")


def pane_command_text(pane_id):
    result = tmux("show-option", "-p", "-v", "-t", pane_id,
                  "@kitty_tmux_command", check=False)
    command = result.stdout.removesuffix("\n") if result.returncode == 0 else ""
    return command if len(command.encode("utf-8")) <= MAX_COMMAND_BYTES else ""


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
        window_id, pane_id, pane_index, *path_active = row.split(SEP)
        cwd = SEP.join(path_active[:-1])
        active = path_active[-1]
        panes_by_window.setdefault(window_id, []).append(
            {"index": int(pane_index), "cwd": cwd, "active": active == "1",
             "history": pane_history(pane_id),
             "command": pane_command_text(pane_id)})
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
    windows = snapshot.get("windows") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1 or not isinstance(windows, list) or not windows:
        preserve_failed_snapshot(project_id, original)
        raise ValueError(f"Invalid snapshot for {project['title']}")
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
            id_format = f"#{{window_id}}{SEP}#{{pane_id}}"
            if not created:
                result = tmux("new-session", "-d", "-P", "-F", id_format,
                              "-s", name, "-x", "240", "-y", "80",
                              "-n", window["title"], "-c", cwd, *command)
                created = True
            else:
                result = tmux("new-window", "-d", "-P", "-F", id_format,
                              "-t", f"{name}:{window['index']}", "-n", window["title"],
                              "-c", cwd, *command)
            window_id, first_pane_id = result.stdout.strip().split(SEP, 1)
            restored_windows[window["index"]] = window_id
            pane_ids = [first_pane_id]
            complete = True
            for pane_number, pane in enumerate(panes[1:], start=1):
                cwd = existing_directory(pane.get("cwd"), project.get("root"))
                result = tmux("split-window", "-d", "-h", "-P", "-F", "#{pane_id}",
                              "-t", window_id, "-c", cwd,
                              *pane_command(project_id, window_number, pane_number), check=False)
                if result.returncode:
                    print(f"Could not restore pane {pane_number + 1} in {window['title']}: "
                          f"{result.stderr.strip()}", file=sys.stderr)
                    complete = False
                    incomplete = True
                    break
                pane_ids.append(result.stdout.strip())
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
        exec_fish()
    kitty_pid = os.environ["KITTY_PID"]
    window_id = os.environ["KITTY_WINDOW_ID"]
    data = read_state()
    project_id = os.environ.get("KITTY_PROJECT_ID")
    project = next((p for p in data["projects"] if p["id"] == project_id), None)
    if project is None:
        os.execvp("tmux", ("tmux", "new-session", "-A", "-s", f"kitty-{kitty_pid}-{window_id}"))
    close_intent_path(project_id).unlink(missing_ok=True)
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
        pane = snapshot["windows"][window_number]["panes"][pane_number]
        history = pane.get("history", "")
        lines = history.split("\n")
        while lines and not SGR.sub("", lines[-1]).strip():
            lines.pop()
        if lines:
            # capture-pane includes unused rows below the old prompt.
            sys.stdout.buffer.write(("\n".join(lines) + "\x1b[0m\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        command = pane.get("command")
        if isinstance(command, str) and command and len(command.encode("utf-8")) <= MAX_COMMAND_BYTES:
            os.environ["KITTY_TMUX_RESTORE_COMMAND"] = command
    except (OSError, ValueError, KeyError, IndexError):
        pass
    exec_fish()


def exec_fish():
    os.execvp("fish", ("fish",))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("attach-auto")
    all_projects = sub.add_parser("snapshot-all")
    all_projects.add_argument("--wait", action="store_true")
    one = sub.add_parser("snapshot-one")
    one.add_argument("project_id")
    cleanup = sub.add_parser("delete-snapshot")
    cleanup.add_argument("project_id")
    close = sub.add_parser("mark-close")
    close.add_argument("kind", choices=("fish", "pane", "window"))
    close.add_argument("pane_id")
    close.add_argument("fish_pid", nargs="?")
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
        snapshot_all(blocking=args.wait)
    elif args.command == "snapshot-one":
        with state_lock("snapshot"):
            project = next((p for p in read_state()["projects"] if p["id"] == args.project_id), None)
            if project:
                snapshot_project(project)
    elif args.command == "delete-snapshot":
        delete_snapshot(args.project_id)
    elif args.command == "mark-close":
        mark_close_intent(args.kind, args.pane_id, args.fish_pid)
    elif args.command == "pane-shell":
        pane_shell(args.project_id, args.window_number, args.pane_number)


if __name__ == "__main__":
    main()
