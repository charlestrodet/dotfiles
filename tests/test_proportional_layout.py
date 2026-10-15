"""Pure layout checks; tmux's socket is unavailable in this test sandbox."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "home/.config/tmux/proportional.py"
spec = importlib.util.spec_from_file_location("proportional", MODULE)
proportional = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = proportional
spec.loader.exec_module(proportional)


def layout(body):
    return proportional.parse("0000," + body)


class ProportionalLayoutTest(unittest.TestCase):
    def test_update_uses_two_tmux_calls_for_split(self):
        before = "0000,102x20,0,0{70x20,0,0,1,31x20,71,0,2}"
        after = "0000,102x20,0,0{70x20,0,0,1,15x20,71,0,2,15x20,87,0,3}"
        calls = []

        def fake_tmux(*args, **_kwargs):
            calls.append(args)
            if args[0] == "display-message":
                return after + proportional.SEP + before
            return ""

        with patch.object(proportional, "tmux", side_effect=fake_tmux):
            proportional.update("@1")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "display-message")
        commands = []
        for arg in calls[1]:
            if arg == ";":
                commands.append([])
            elif not commands:
                commands.append([arg])
            else:
                commands[-1].append(arg)
        self.assertEqual(commands[0],
                         ["set-option", "-w", "-t", "@1", proportional.LAYOUT_OPTION, after])
        self.assertEqual(commands[1],
                         ["set-option", "-w", "-t", "@1", proportional.COUNT_OPTION, "3"])
        self.assertEqual(commands[2:4],
                         [["resize-pane", "-t", "%1", "-x", "46"],
                          ["resize-pane", "-t", "%2", "-x", "21"]])
        self.assertEqual(commands[4],
                         ["set-option", "-w", "-F", "-t", "@1",
                          proportional.LAYOUT_OPTION, "#{window_layout}"])

    def test_first_layout_still_seeds_without_resizing(self):
        layout = "0000,100x20,0,0,1"
        calls = []

        def fake_tmux(*args, **_kwargs):
            calls.append(args)
            if args[0] == "display-message":
                return layout + proportional.SEP
            return ""

        with patch.object(proportional, "tmux", side_effect=fake_tmux):
            proportional.update("@1")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1],
                         ("set-option", "-w", "-t", "@1", proportional.LAYOUT_OPTION,
                          layout, ";", "set-option", "-w", "-t", "@1",
                          proportional.COUNT_OPTION, "1"))

    def test_split_reserves_one_share_and_keeps_old_ratio(self):
        before = layout("102x20,0,0{70x20,0,0,1,31x20,71,0,2}")
        after = layout("102x20,0,0{70x20,0,0,1,15x20,71,0,2,15x20,87,0,3}")
        self.assertEqual(proportional.plan(before, after), [(1, "h", 46), (2, "h", 21)])
        # The final (new) pane receives 100 - 46 - 21 = 33 cells.

    def test_close_reallocates_by_survivors_existing_sizes(self):
        before = layout("100x20,0,0{49x20,0,0,1,29x20,50,0,2,20x20,80,0,3}")
        after = layout("100x20,0,0{79x20,0,0,1,20x20,80,0,3}")
        self.assertEqual(proportional.plan(before, after), [(1, "h", 70)])
        # 70/29 is the closest integer ratio to 49/20 over 99 cells.

    def test_split_in_the_middle_uses_last_pane_for_remainder(self):
        before = layout("102x20,0,0{70x20,0,0,1,31x20,71,0,2}")
        after = layout("102x20,0,0{35x20,0,0,1,34x20,36,0,3,31x20,71,0,2}")
        self.assertEqual(proportional.plan(before, after), [(1, "h", 46), (3, "h", 33)])

    def test_closing_first_pane_keeps_the_other_two_in_proportion(self):
        before = layout("100x20,0,0{49x20,0,0,1,29x20,50,0,2,20x20,80,0,3}")
        after = layout("100x20,0,0{79x20,0,0,2,20x20,80,0,3}")
        self.assertEqual(proportional.plan(before, after), [(2, "h", 59)])

    def test_mixed_layout_changes_only_the_local_row_group(self):
        before = layout("100x20,0,0{60x20,0,0,1,39x20,61,0[39x10,61,0,2,39x9,61,11,3]}")
        after = layout("100x20,0,0{60x20,0,0,1,39x20,61,0[39x10,61,0,2,39x4,61,11,3,39x4,61,16,4]}")
        self.assertEqual(proportional.plan(before, after), [(2, "v", 6), (3, "v", 6)])

    def test_manual_resize_does_not_trigger_redistribution(self):
        before = layout("100x20,0,0{49x20,0,0,1,50x20,50,0,2}")
        after = layout("100x20,0,0{70x20,0,0,1,29x20,71,0,2}")
        self.assertEqual(proportional.plan(before, after), [])

    def test_minimums_are_honoured(self):
        self.assertEqual(proportional.shares(10, [9, 1], [4, 4]), [6, 4])


if __name__ == "__main__":
    unittest.main()
