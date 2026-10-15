import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


KITTY_DIR = Path(__file__).resolve().parents[1] / "home/.config/kitty"
sys.path.insert(0, str(KITTY_DIR))
state = importlib.import_module("project_state")
watcher = importlib.import_module("project_watcher")

kitten_pkg = types.ModuleType("kittens")
kitten_pkg.__path__ = []
kitten_tui = types.ModuleType("kittens.tui")
kitten_tui.__path__ = []
kitten_handler = types.ModuleType("kittens.tui.handler")
def result_handler(*, no_ui):
    def decorate(callback):
        callback.no_ui = no_ui
        return callback
    return decorate
kitten_handler.result_handler = result_handler
with patch.dict(sys.modules, {"kittens": kitten_pkg, "kittens.tui": kitten_tui,
                              "kittens.tui.handler": kitten_handler}):
    title_kitten = importlib.import_module("project_title")


class Window:
    def __init__(self, window_id=7, os_window_id=3, env=None):
        self.id = window_id
        self.os_window_id = os_window_id
        self.child = type("Child", (), {"environ": env or {}})()
        self.tab = None

    def tabref(self):
        return self.tab


class Tab:
    def __init__(self, name, window):
        self.id = 5
        self.name = name
        self.title = name or "fish"
        self.active_window = window
        self.windows = [window]
        window.tab = self

    def get_cwd_of_active_window(self):
        return "/tmp"

    def set_title(self, title):
        self.name = title


class Manager:
    def __init__(self, tab):
        self.os_window_id = 3
        self.tabs = [tab]


class ProjectStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original = state.STATE_DIR, state.PROJECTS_FILE, state.STARTUP_FILE
        state.STATE_DIR = Path(self.temp.name)
        state.PROJECTS_FILE = state.STATE_DIR / "projects.json"
        state.STARTUP_FILE = state.STATE_DIR / "startup.kitty-session"
        self.addCleanup(self.restore_paths)
        watcher._tabs.clear()
        watcher._closed_os_windows.clear()
        watcher._orders.clear()
        snapshot_patch = patch.object(watcher, "snapshot_in_background")
        kill_patch = patch.object(watcher, "kill_session_in_background")
        self.snapshot = snapshot_patch.start()
        self.kill = kill_patch.start()
        self.addCleanup(kill_patch.stop)
        self.addCleanup(snapshot_patch.stop)
        state.initialize()

    def restore_paths(self):
        state.STATE_DIR, state.PROJECTS_FILE, state.STARTUP_FILE = self.original

    def test_last_tab_close_removes_project_but_os_window_close_keeps_it(self):
        window = Window()
        tab = Tab("Projet", window)
        manager = Manager(tab)
        boss = type("Boss", (), {"os_window_map": {3: manager}})()
        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
        project_id = state.read_state()["projects"][0]["id"]
        watcher.on_close(boss, window, {})
        self.assertEqual(state.read_state()["projects"], [])
        self.assertNotIn("new_tab", state.STARTUP_FILE.read_text())
        self.snapshot.assert_called_with("delete-snapshot", project_id)
        self.kill.assert_called_with(state.session_name(project_id))

        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
        self.assertEqual(len(state.read_state()["projects"]), 1)
        boss.os_window_map.clear()
        watcher.on_close(boss, window, {})
        self.snapshot.assert_called_with("snapshot-all")
        self.assertEqual(len(state.read_state()["projects"]), 1)

    def test_forget_does_not_wait_for_capture_to_delete_snapshot(self):
        project_id, _ = state.register("Projet", "/tmp", 1, 2)
        snapshot = state.STATE_DIR / f"{project_id}.json"
        recovery = state.STATE_DIR / f"{project_id}.recovery.json"
        snapshot.write_text("{}")
        recovery.write_text("{}")
        original_lock = state.state_lock

        def reject_snapshot_lock(name="state", blocking=True):
            if name == "snapshot":
                raise AssertionError("The Kitty watcher must not wait for a pane capture")
            return original_lock(name, blocking)

        with patch.object(state, "state_lock", side_effect=reject_snapshot_lock):
            state.forget(project_id, 1, 2)
        self.assertTrue(snapshot.exists())
        self.assertEqual(state.read_state()["projects"], [])
        state.delete_snapshot(project_id)
        self.assertFalse(snapshot.exists())
        self.assertFalse(recovery.exists())

    def test_rename_and_reset_title(self):
        window = Window()
        tab = Tab("Projet", window)
        manager = Manager(tab)
        boss = type("Boss", (), {"os_window_map": {3: manager}})()
        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            project_id = state.read_state()["projects"][0]["id"]
            window.child.environ["KITTY_PROJECT_ID"] = project_id
            tab.name = "Autre nom"
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertEqual(state.read_state()["projects"][0]["id"], project_id)
            self.assertEqual(state.read_state()["projects"][0]["title"], "Autre nom")
            tab.name = ""
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertEqual(state.read_state()["projects"], [])
            tab.name = "Projet repris"
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertNotEqual(state.read_state()["projects"][0]["id"], project_id)

    def test_unchanged_tab_bar_does_not_recheck_project_order(self):
        window = Window()
        tab = Tab("Projet", window)
        manager = Manager(tab)
        boss = type("Boss", (), {"os_window_map": {3: manager}})()
        with patch.object(state, "tmux_has_session", return_value=False), \
             patch.object(state, "reorder", wraps=state.reorder) as reorder:
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
        reorder.assert_called_once()

    def test_closing_temporary_tab_ends_its_tmux_session(self):
        window = Window()
        tab = Tab("", window)
        boss = type("Boss", (), {"os_window_map": {3: Manager(tab)}})()
        watcher.on_close(boss, window, {})
        self.kill.assert_called_once_with(f"kitty-{watcher.os.getpid()}-7")

    def test_rename_kitten_immediately_registers_and_renames_session(self):
        window = Window(env={"KITTY_TMUX_PROJECTS": "1"})
        tab = Tab("", window)
        manager = Manager(tab)
        class Boss:
            window_id_map = {7: window}
            os_window_map = {3: manager}

            def get_line(self, message, callback, **kwargs):
                self.assert_prompt = message
                callback("Debian")

        boss = Boss()
        result = type("Result", (), {"returncode": 0, "stdout": "/tmp\n"})()
        with patch.object(state, "tmux_has_session", return_value=True), \
             patch.object(state, "tmux", return_value=result) as tmux, \
             patch.object(title_kitten, "has_tab_change_event", return_value=False):
            title_kitten.handle_result([], None, 7, boss)
        project_id = state.read_state()["projects"][0]["id"]
        self.assertEqual(tab.name, "Debian")
        self.assertIn("new_tab Debian", state.STARTUP_FILE.read_text())
        tmux.assert_any_call("rename-session", "-t", f"=kitty-{watcher.os.getpid()}-7",
                             state.session_name(project_id))
        self.snapshot.assert_called_with("snapshot-one", project_id)

    def test_rename_kitten_leaves_plain_kitty_unwatched(self):
        window = Window()
        tab = Tab("", window)
        manager = Manager(tab)
        class Boss:
            window_id_map = {7: window}
            os_window_map = {3: manager}

            def get_line(self, message, callback, **kwargs):
                callback("Simple")

        title_kitten.handle_result([], None, 7, Boss())
        self.assertEqual(tab.name, "Simple")
        self.assertEqual(state.read_state()["projects"], [])

    def test_close_after_watcher_reload_uses_runtime_binding(self):
        window = Window()
        tab = Tab("Projet", window)
        manager = Manager(tab)
        boss = type("Boss", (), {"os_window_map": {3: manager}})()
        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
        watcher._tabs.clear()  # Kitty can reload its watcher with the tab still open.
        watcher.on_close(boss, window, {})
        self.assertEqual(state.read_state()["projects"], [])

    def test_snapshot_keeps_layout_cwd_and_scrollback(self):
        project_id, _ = state.register("Projet", "/tmp", 1, 2)
        project = state.read_state()["projects"][0]

        def fake_tmux(*args, **kwargs):
            if args[0] == "has-session":
                return type("Result", (), {"returncode": 0, "stdout": ""})()
            if args[0] == "list-windows":
                output = SEP.join(("@1", "1", "editor", "layout-string", "1", "0")) + "\n"
            elif args[0] == "list-panes":
                output = SEP.join(("@1", "%1", "1", "/tmp", "1")) + "\n"
            else:
                output = "ancien affichage\n"
            return type("Result", (), {"returncode": 0, "stdout": output})()

        SEP = state.SEP
        with patch.object(state, "tmux", side_effect=fake_tmux) as tmux:
            self.assertTrue(state.snapshot_project(project))
        self.assertEqual(tmux.call_count, 4)
        snapshot = state.STATE_DIR / f"{project_id}.json"
        data = state.json.loads(snapshot.read_text())
        self.assertEqual(data["windows"][0]["layout"], "layout-string")
        self.assertEqual(data["windows"][0]["panes"][0]["cwd"], "/tmp")
        self.assertIn("ancien affichage", data["windows"][0]["panes"][0]["history"])

    def test_attach_auto_reuses_a_live_project_session(self):
        project_id, _ = state.register("Projet", "/tmp", 1, 2)
        environment = {"KITTY_PID": "1", "KITTY_WINDOW_ID": "2",
                       "KITTY_PROJECT_ID": project_id}
        with patch.dict(state.os.environ, environment, clear=True), \
             patch.object(state, "tmux_has_session", return_value=True), \
             patch.object(state, "restore_project") as restore, \
             patch.object(state.os, "execvp") as execvp:
            state.attach_auto()
        restore.assert_not_called()
        execvp.assert_called_once_with(
            "tmux", ("tmux", "new-session", "-A", "-s", state.session_name(project_id),
                     "-c", "/tmp"))

    def test_restore_recreates_panes_layout_and_focus_without_old_programs(self):
        project_id, _ = state.register("Projet", "/tmp", 1, 2)
        project = state.read_state()["projects"][0]
        snapshot = {"version": 1, "windows": [
            {"index": 1, "title": "code", "layout": "saved-layout", "active": False,
             "zoomed": False, "panes": [
                 {"index": 1, "cwd": "/tmp", "active": False, "history": "avant\n"},
                 {"index": 2, "cwd": "/tmp", "active": True, "history": "autre\n"}]},
            {"index": 2, "title": "tests", "layout": "second-layout", "active": True,
             "zoomed": False, "panes": [
                 {"index": 1, "cwd": "/tmp", "active": True, "history": "résultat\n"}]},
        ]}
        (state.STATE_DIR / f"{project_id}.json").write_text(state.json.dumps(snapshot))
        commands = []
        windows = []
        panes = {}

        def fake_tmux(*args, **kwargs):
            commands.append(args)
            action = args[0]
            if action == "new-session":
                windows.append("@1")
                panes["@1"] = ["%1"]
            elif action == "new-window":
                windows.append("@2")
                panes["@2"] = ["%3"]
            elif action == "split-window":
                panes[args[args.index("-t") + 1]].append("%2")
            if action == "list-windows":
                output = "\n".join(windows) + "\n"
            elif action == "list-panes":
                output = "\n".join(panes[args[args.index("-t") + 1]]) + "\n"
            else:
                output = ""
            return type("Result", (), {"returncode": 0, "stdout": output})()

        with patch.object(state, "tmux_has_session", return_value=False), patch.object(state, "tmux", side_effect=fake_tmux):
            state.restore_project(project)
        self.assertIn(("select-layout", "-t", "@1", "saved-layout"), commands)
        self.assertIn(("select-pane", "-t", "%2"), commands)
        self.assertIn(("select-window", "-t", "@2"), commands)
        starts = [cmd for cmd in commands if cmd[0] in ("new-session", "new-window", "split-window")]
        self.assertEqual(len(starts), 3)
        self.assertTrue(all("pane-shell" in cmd for cmd in starts))

    def test_restore_skips_bad_window_and_falls_back_from_bad_layout(self):
        project_id, _ = state.register("Projet", "/tmp", 1, 2)
        project = state.read_state()["projects"][0]
        snapshot = {"version": 1, "windows": [
            {"index": 1, "title": "incomplete"},
            {"index": 2, "title": "bad layout", "layout": "invalid", "active": True,
             "panes": [{"cwd": "/tmp", "active": True}]},
            {"index": 3, "title": "good", "layout": "valid", "active": False,
             "panes": [{"cwd": "/tmp", "active": True}]},
        ]}
        (state.STATE_DIR / f"{project_id}.json").write_text(state.json.dumps(snapshot))
        commands = []
        windows = []

        def fake_tmux(*args, **kwargs):
            commands.append(args)
            action = args[0]
            if action == "new-session":
                windows.append("@1")
            elif action == "new-window":
                windows.append("@2")
            output = ""
            if action == "list-windows":
                output = "\n".join(windows) + "\n"
            elif action == "list-panes":
                output = "%1\n" if args[args.index("-t") + 1] == "@1" else "%2\n"
            failed = action == "select-layout" and args[-1] == "invalid"
            return type("Result", (), {"returncode": int(failed), "stdout": output,
                                        "stderr": "invalid layout" if failed else ""})()

        with patch.object(state, "tmux_has_session", return_value=False), \
             patch.object(state, "tmux", side_effect=fake_tmux), \
             patch.object(state.sys, "stderr"):
            state.restore_project(project)
        self.assertIn(("select-layout", "-t", "@1", "tiled"), commands)
        self.assertIn(("select-layout", "-t", "@2", "valid"), commands)
        self.assertIn(("select-window", "-t", "@1"), commands)
        self.assertNotIn("kill-session", [cmd[0] for cmd in commands])
        recovery = state.STATE_DIR / f"{project_id}.recovery.json"
        self.assertEqual(state.json.loads(recovery.read_text()), snapshot)

    def test_pane_shell_replays_content_without_unused_bottom_rows(self):
        snapshot = {"windows": [{"panes": [{"history": "avant\n\nfin\n \n\n"}]}]}
        (state.STATE_DIR / "project.json").write_text(state.json.dumps(snapshot))
        with patch.object(state.sys, "stdout") as output, \
             patch.object(state.os, "execvp") as execvp, \
             patch.dict(state.os.environ, {"SHELL": "/bin/bash"}):
            state.pane_shell("project", 0, 0)
        output.buffer.write.assert_called_once_with(b"avant\n\nfin\n")
        execvp.assert_called_once_with("/bin/bash", ("/bin/bash",))


if __name__ == "__main__":
    unittest.main()
