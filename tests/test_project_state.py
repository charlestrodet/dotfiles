import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


KITTY_DIR = Path(__file__).resolve().parents[1] / "home/.config/kitty"
sys.path.insert(0, str(KITTY_DIR))
state = importlib.import_module("project_state")
watcher = importlib.import_module("project_watcher")


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
        self.active_window = window
        self.windows = [window]
        window.tab = self

    def get_cwd_of_active_window(self):
        return "/tmp"


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
        self.assertEqual(len(state.read_state()["projects"]), 1)
        watcher.on_close(boss, window, {})
        self.assertEqual(state.read_state()["projects"], [])
        self.assertNotIn("new_tab", state.STARTUP_FILE.read_text())

        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
        self.assertEqual(len(state.read_state()["projects"]), 1)
        boss.os_window_map.clear()
        with patch.object(state, "snapshot_all") as save:
            watcher.on_close(boss, window, {})
            save.assert_called_once()
        self.assertEqual(len(state.read_state()["projects"]), 1)

    def test_rename_and_reset_title(self):
        window = Window()
        tab = Tab("Projet", window)
        manager = Manager(tab)
        boss = type("Boss", (), {"os_window_map": {3: manager}})()
        with patch.object(state, "tmux_has_session", return_value=False):
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            project_id = state.read_state()["projects"][0]["id"]
            tab.name = "Autre nom"
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertEqual(state.read_state()["projects"][0]["id"], project_id)
            self.assertEqual(state.read_state()["projects"][0]["title"], "Autre nom")
            tab.name = ""
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertEqual(state.read_state()["projects"], [])
            tab.name = "Projet repris"
            watcher.on_tab_bar_dirty(boss, window, {"tab_manager": manager})
            self.assertEqual(state.read_state()["projects"][0]["id"], project_id)

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
                output = "@1\n"
            elif args[0] == "display-message" and args[-1].startswith("#{window_index}"):
                output = SEP.join(("1", "editor", "layout-string", "1", "0")) + "\n"
            elif args[0] == "list-panes":
                output = "%1\n"
            elif args[0] == "display-message":
                output = SEP.join(("1", "/tmp", "1")) + "\n"
            else:
                output = "ancien affichage\n"
            return type("Result", (), {"returncode": 0, "stdout": output})()

        SEP = state.SEP
        with patch.object(state, "tmux", side_effect=fake_tmux):
            self.assertTrue(state.snapshot_project(project))
        snapshot = state.STATE_DIR / f"{project_id}.json"
        data = state.json.loads(snapshot.read_text())
        self.assertEqual(data["windows"][0]["layout"], "layout-string")
        self.assertEqual(data["windows"][0]["panes"][0]["cwd"], "/tmp")
        self.assertIn("ancien affichage", data["windows"][0]["panes"][0]["history"])

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

    def test_pane_shell_replays_content_without_unused_bottom_rows(self):
        snapshot = {"windows": [{"panes": [{"history": "avant\n\nfin\n \n\n"}]}]}
        (state.STATE_DIR / "project.json").write_text(state.json.dumps(snapshot))
        with patch.object(state.sys, "stdout") as output, patch.object(state.os, "execvp") as execvp:
            state.pane_shell("project", 0, 0)
        output.buffer.write.assert_called_once_with(b"avant\n\nfin\n")
        execvp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
