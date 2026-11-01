import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


SNIPPET = Path(__file__).resolve().parents[1] / "home/.config/fish/conf.d/kitty-tmux.fish"
sys.path.insert(0, str(SNIPPET.parents[2] / "kitty"))
import project_state as state


@unittest.skipUnless(shutil.which("fish"), "fish is required")
class FishRestoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup_root)
        config = self.root / "config/fish/conf.d"
        config.mkdir(parents=True)
        shutil.copy(SNIPPET, config / SNIPPET.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.env = {**os.environ, "HOME": str(self.root),
                    "XDG_CONFIG_HOME": str(self.root / "config"),
                    "KITTY_TMUX_STATE_DIR": str(self.state),
                    "KITTY_TMUX_PROJECTS": "1", "TMUX": "/tmp/test,123,0",
                    "TMUX_PANE": "%1", "TERM": "dumb"}

    def cleanup_root(self):
        # Fish can still finish writing its completion cache after tmux exits.
        for _ in range(20):
            try:
                shutil.rmtree(self.root)
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.1)
        shutil.rmtree(self.root)

    def start_fish(self, extra=None):
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(shutil.which("fish"), ["fish", "-i"], {**self.env, **(extra or {})})
        self.addCleanup(self.stop_fish, pid, fd)
        return pid, fd

    @staticmethod
    def stop_fish(pid, fd):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(fd)

    @staticmethod
    def read_until(fd, condition, timeout=4):
        output = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    output += os.read(fd, 65536)
                except OSError:
                    break
            if condition(output):
                return output
        raise AssertionError(f"fish did not reach expected state: {output[-300:]!r}")

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required")
    def test_running_command_is_saved_then_cleared(self):
        socket = f"codex-command-{uuid.uuid4().hex[:8]}"
        env = {**self.env, "TERM": "xterm-256color"}
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)

        def tmux(*args, check=True):
            return subprocess.run(("tmux", "-L", socket, "-f", "/dev/null", *args),
                                  env=env, capture_output=True, text=True, check=check)

        try:
            tmux("new-session", "-d", "-s", "test", "-c", str(self.root), "/usr/bin/fish")
            pane = tmux("list-panes", "-t", "test", "-F", "#{pane_id}").stdout.strip()
            tmux("send-keys", "-t", pane, "sleep 2", "Enter")

            def saved_command():
                return tmux("show-option", "-p", "-v", "-t", pane,
                            "@kitty_tmux_command", check=False).stdout.strip()

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and saved_command() != "sleep 2":
                time.sleep(0.05)
            self.assertEqual(saved_command(), "sleep 2")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and saved_command():
                time.sleep(0.05)
            self.assertEqual(saved_command(), "")
            tmux("set-option", "-p", "-t", pane, "@kitty_tmux_command", "stale")
            tmux("send-keys", "-t", pane, "exec fish", "Enter")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and saved_command():
                time.sleep(0.05)
            self.assertEqual(saved_command(), "")
        finally:
            tmux("kill-server", check=False)

    def test_restored_command_stays_in_editor(self):
        marker = self.root / "should-not-exist"
        command = f"touch {marker}"
        pid, fd = self.start_fish({"KITTY_TMUX_RESTORE_COMMAND": command})
        self.read_until(fd, lambda output: command.encode() in output)
        self.assertFalse(marker.exists())
        self.assertEqual(os.waitpid(pid, os.WNOHANG), (0, 0))

    @unittest.skipUnless(shutil.which("tmux"), "tmux is required")
    def test_tmux_shutdown_restores_command_and_colors(self):
        socket = f"codex-restore-{uuid.uuid4().hex[:8]}"
        env = {**self.env, "TERM": "xterm-256color"}
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)

        def tmux(*args, check=True):
            return subprocess.run(("tmux", "-L", socket, "-f", "/dev/null", *map(str, args)),
                                  env=env, capture_output=True, text=True, check=check)

        project = {"id": uuid.uuid4().hex, "title": "test", "root": str(self.root)}
        session = state.session_name(project["id"])
        command = "printf '\\e[31mred\\e[0m\\n'; sleep 30"
        with patch.object(state, "STATE_DIR", self.state), patch.object(state, "tmux", tmux):
            try:
                tmux("new-session", "-d", "-s", session, "-x", "100", "-y", "20",
                     "-c", str(self.root), "/usr/bin/fish")
                pane_id = tmux("list-panes", "-t", session, "-F", "#{pane_id}").stdout.strip()
                tmux("send-keys", "-t", session, command, "Enter")

                def running_command():
                    result = tmux("show-option", "-p", "-v", "-t", pane_id,
                                  "@kitty_tmux_command", check=False)
                    return result.stdout.removesuffix("\n") if result.returncode == 0 else None

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and running_command() != command:
                    time.sleep(0.05)
                self.assertEqual(running_command(), command)
                self.assertTrue(state.snapshot_project(project))
                snapshot = json.loads((self.state / f"{project['id']}.json").read_text())
                pane = snapshot["windows"][0]["panes"][0]
                self.assertEqual(pane["command"], command)
                self.assertIn("\x1b[31m", pane["history"])

                tmux("kill-server")
                time.sleep(0.3)
                state.restore_project(project)

                deadline = time.monotonic() + 5
                output = ""
                while time.monotonic() < deadline:
                    output = tmux("capture-pane", "-e", "-p", "-S", "-", "-t", session).stdout
                    if state.SGR.sub("", output).count("sleep 30") >= 2:
                        break
                    time.sleep(0.05)
                self.assertGreaterEqual(state.SGR.sub("", output).count("sleep 30"), 2,
                                        f"{output[-400:]!r}")
                self.assertIn("\x1b[31m", output)
                restored_pane = tmux("list-panes", "-t", session, "-F", "#{pane_id}").stdout.strip()
                restored_command = tmux("show-option", "-p", "-v", "-t", restored_pane,
                                        "@kitty_tmux_command").stdout.removesuffix("\n")
                self.assertEqual(restored_command, command)
                self.assertFalse(list(self.state.glob(".command-*")))
            finally:
                tmux("kill-server", check=False)
                time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
