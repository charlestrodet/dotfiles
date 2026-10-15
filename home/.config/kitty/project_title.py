"""Rename a Kitty tab and register projects on Kitty without tab-change events."""

import sys
from gettext import gettext as _
from pathlib import Path

from kittens.tui.handler import result_handler

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(args):
    pass


def has_tab_change_event():
    from kitty.window import Watchers
    return hasattr(Watchers(), "on_tab_bar_dirty")


@result_handler(no_ui=True)
def handle_result(args, answer, target_window_id, boss):
    window = boss.window_id_map.get(target_window_id)
    if window is None:
        return
    tab = window.tabref()
    if tab is None:
        return

    def renamed(title):
        tab.set_title(title)
        if window.child.environ.get("KITTY_TMUX_PROJECTS") != "1":
            return
        if not has_tab_change_event():
            import project_watcher
            manager = boss.os_window_map.get(window.os_window_id)
            if manager is not None:
                project_watcher.reconcile_tabs(manager)

    boss.get_line(
        _("Enter the new title for this tab below. An empty title will cause the default title to be used."),
        renamed, window=window, initial_value=tab.name or tab.title,
    )
