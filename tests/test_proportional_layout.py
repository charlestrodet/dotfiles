"""Pure layout checks; tmux's socket is unavailable in this test sandbox."""

import importlib.util
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "home/.config/tmux/proportional.py"
spec = importlib.util.spec_from_file_location("proportional", MODULE)
proportional = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = proportional
spec.loader.exec_module(proportional)


def layout(body):
    return proportional.parse("0000," + body)


class ProportionalLayoutTest(unittest.TestCase):
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
