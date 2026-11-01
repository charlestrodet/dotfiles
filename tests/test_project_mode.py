import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "home/.config/kitty/project_mode.py"


class ProjectModeTest(unittest.TestCase):
    def test_launch_drops_parent_tmux_context(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "KITTY_TMUX_STATE_DIR": directory,
                   "TMUX": "/tmp/other-server,1,0", "TMUX_PANE": "%9"}
            code = "import os,runpy,sys; runpy.run_path(sys.argv[1]); print(os.getenv('TMUX'), os.getenv('TMUX_PANE'))"
            result = subprocess.run((sys.executable, "-c", code, str(SCRIPT)),
                                    env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines()[-1], "None None")

    def test_only_first_process_keeps_project_role_until_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "KITTY_TMUX_STATE_DIR": directory}
            projects = [{"id": "a" * 32, "title": "one", "root": directory},
                        {"id": "b" * 32, "title": "two", "root": directory}]
            Path(directory, "projects.json").write_text(json.dumps(
                {"version": 1, "projects": projects}))
            Path(directory, "startup.kitty-session").write_text("stale\n")
            first_code = (
                "import runpy,sys; "
                "runpy.run_path(sys.argv[1]); runpy.run_path(sys.argv[1]); "
                "print('READY', flush=True); sys.stdin.readline()"
            )
            plain_code = (
                "import runpy,sys; "
                "runpy.run_path(sys.argv[1]); print('READY', flush=True); "
                "sys.stdin.readline(); runpy.run_path(sys.argv[1]); "
                "print('RELOADED', flush=True)"
            )
            first = subprocess.Popen(
                (sys.executable, "-u", "-c", first_code, str(SCRIPT)),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            try:
                title_map = f"map kitty_mod+alt+t kitten {shlex.quote(str(SCRIPT.resolve().with_name('project_title.py')))}\n"
                profile = ("include kitty-projects.conf\n"
                           f"shell python3 {shlex.quote(str(SCRIPT.resolve().with_name('project_state.py')))} attach-auto\n"
                           f"startup_session {shlex.quote(str(Path(directory) / 'startup.kitty-session'))}\n"
                           + title_map)
                self.assertEqual("".join(first.stdout.readline() for _ in range(4)), profile)
                self.assertEqual(Path(directory, "startup.kitty-session").read_text().count("new_tab "), 2)
                self.assertEqual("".join(first.stdout.readline() for _ in range(4)), profile)
                self.assertEqual(first.stdout.readline(), "READY\n")

                plain = subprocess.Popen(
                    (sys.executable, "-u", "-c", plain_code, str(SCRIPT)),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env,
                )
                try:
                    self.assertEqual(plain.stdout.readline(), "READY\n")
                    first.communicate("\n", timeout=5)
                    self.assertEqual(first.returncode, 0)
                    output, error = plain.communicate("\n", timeout=5)
                    self.assertEqual(plain.returncode, 0, error)
                    self.assertEqual(output, "RELOADED\n")
                finally:
                    if plain.poll() is None:
                        plain.kill()
                        plain.communicate()

                next_launch = subprocess.run(
                    (sys.executable, "-u", "-c", "import runpy,sys; runpy.run_path(sys.argv[1])", str(SCRIPT)),
                    capture_output=True, text=True, env=env, timeout=5, check=True,
                )
                self.assertEqual(next_launch.stdout, profile)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.communicate()


if __name__ == "__main__":
    unittest.main()
