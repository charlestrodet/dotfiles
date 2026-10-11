"""Keep sibling pane sizes proportional when tmux adds or removes a pane."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field


LAYOUT_OPTION = "@kitty_tmux_proportional_layout"
COUNT_OPTION = "@kitty_tmux_proportional_count"
CELL = re.compile(r"(\d+)x(\d+),(\d+),(\d+)")


@dataclass
class Cell:
    width: int
    height: int
    pane: int | None = None
    axis: str | None = None  # h: left/right, v: top/bottom
    children: list[Cell] = field(default_factory=list)

    def panes(self) -> frozenset[int]:
        if self.pane is not None:
            return frozenset((self.pane,))
        return frozenset().union(*(child.panes() for child in self.children))

    def size(self, axis: str) -> int:
        return self.width if axis == "h" else self.height

    def first_pane(self) -> int:
        if self.pane is not None:
            return self.pane
        return self.children[0].first_pane()

    def minimum(self, axis: str) -> int:
        if self.pane is not None:
            return 1
        sizes = [child.minimum(axis) for child in self.children]
        return sum(sizes) + len(sizes) - 1 if self.axis == axis else max(sizes)


def parse(layout: str) -> Cell:
    """Read tmux's window_layout tree; pane suffixes are stable pane IDs."""
    _, body = layout.split(",", 1)
    pos = 0

    def cell() -> Cell:
        nonlocal pos
        match = CELL.match(body, pos)
        if match is None:
            raise ValueError("invalid tmux layout cell")
        width, height, _, _ = map(int, match.groups())
        pos = match.end()
        node = Cell(width, height)
        if pos < len(body) and body[pos] in "{[":
            opening = body[pos]
            closing = "}" if opening == "{" else "]"
            node.axis = "h" if opening == "{" else "v"
            pos += 1
            while True:
                node.children.append(cell())
                if pos >= len(body):
                    raise ValueError("unterminated tmux layout group")
                if body[pos] == closing:
                    pos += 1
                    break
                if body[pos] != ",":
                    raise ValueError("invalid tmux layout separator")
                pos += 1
        else:
            if pos >= len(body) or body[pos] != ",":
                raise ValueError("missing tmux pane ID")
            pos += 1
            end = pos
            while pos < len(body) and body[pos].isdigit():
                pos += 1
            if pos == end:
                raise ValueError("invalid tmux pane ID")
            node.pane = int(body[end:pos])
        return node

    root = cell()
    if pos != len(body):
        raise ValueError("trailing tmux layout data")
    return root


def walk(node: Cell):
    yield node
    for child in node.children:
        yield from walk(child)


def parent_of(node: Cell, pane: int) -> Cell | None:
    for child in node.children:
        if child.pane == pane:
            return node
        parent = parent_of(child, pane)
        if parent is not None:
            return parent
    return None


def matching(node: Cell, panes: frozenset[int]) -> Cell | None:
    return next((cell for cell in walk(node) if cell.panes() == panes), None)


def shares(total: int, weights: list[int], minimums: list[int]) -> list[int]:
    """Largest-remainder allocation with each subtree's minimum size."""
    if total < sum(minimums):
        raise ValueError("not enough room for pane minimums")
    result = [0] * len(weights)
    pending = set(range(len(weights)))
    remaining = total
    while pending:
        weight_sum = sum(weights[i] for i in pending)
        quotas = {i: remaining * weights[i] / weight_sum for i in pending}
        pinned = {i for i in pending if quotas[i] < minimums[i]}
        if not pinned:
            for i in pending:
                result[i] = int(quotas[i])
            left = remaining - sum(result[i] for i in pending)
            order = sorted(pending, key=lambda i: (-(quotas[i] - result[i]), i))
            for i in order[:left]:
                result[i] += 1
            break
        for i in pinned:
            result[i] = minimums[i]
            remaining -= result[i]
        pending -= pinned
    return result


def plan(before: Cell, after: Cell) -> list[tuple[int, str, int]]:
    """Return absolute pane sizes for the changed sibling group only."""
    added = after.panes() - before.panes()
    removed = before.panes() - after.panes()
    if len(added) == 1 and not removed:
        new_pane = next(iter(added))
        group = parent_of(after, new_pane)
        if group is None:
            return []
        axis = group.axis
        new_index = next(i for i, c in enumerate(group.children) if new_pane in c.panes())
        other = [c for i, c in enumerate(group.children) if i != new_index]
        old = [matching(before, c.panes()) for c in other]
        if any(c is None for c in old):
            return []
        available = group.size(axis) - len(group.children) + 1
        new_min = group.children[new_index].minimum(axis)
        other_min = [c.minimum(axis) for c in other]
        new_size = min(max(round(available / len(group.children)), new_min),
                       available - sum(other_min))
        if new_size < new_min:
            return []
        old_sizes = shares(available - new_size,
                           [c.size(axis) for c in old], other_min)
        targets = iter(old_sizes)
        sizes = [new_size if i == new_index else next(targets)
                 for i in range(len(group.children))]
    elif len(removed) == 1 and not added:
        old_pane = next(iter(removed))
        old_group = parent_of(before, old_pane)
        if old_group is None or len(old_group.children) < 3:
            return []
        group = matching(after, old_group.panes() - {old_pane})
        if group is None or group.axis != old_group.axis:
            return []
        axis = group.axis
        old = [matching(old_group, c.panes()) for c in group.children]
        if any(c is None for c in old):
            return []
        available = group.size(axis) - len(group.children) + 1
        sizes = shares(available, [c.size(axis) for c in old],
                       [c.minimum(axis) for c in group.children])
    else:
        return []
    # The final sibling receives the remainder naturally. Setting the others
    # with resize-pane preserves pane identities, even after swap-pane.
    return [(cell.first_pane(), axis, size)
            for cell, size in zip(group.children[:-1], sizes[:-1])]


def tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(("tmux", *args), text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def current(window: str) -> str:
    return tmux("display-message", "-p", "-t", window, "#{window_layout}")


def remember(window: str, layout: str) -> None:
    tmux("set-option", "-w", "-t", window, LAYOUT_OPTION, layout)
    tmux("set-option", "-w", "-t", window, COUNT_OPTION, str(len(parse(layout).panes())))


def update(window: str) -> None:
    try:
        layout = current(window)
    except RuntimeError:  # The final pane may have destroyed its window.
        return
    previous = tmux("show-options", "-w", "-v", "-t", window,
                    LAYOUT_OPTION, check=False)
    if not previous:
        remember(window, layout)
        return
    try:
        changes = plan(parse(previous), parse(layout))
    except ValueError:
        changes = []
    remember(window, layout)  # Subsequent resize events must not replay this change.
    flag = "-x" if changes and changes[0][1] == "h" else "-y"
    for pane, _, size in changes:
        tmux("resize-pane", "-t", f"%{pane}", flag, str(size))
    if changes:
        try:
            remember(window, current(window))
        except RuntimeError:
            pass


def seed_all() -> None:
    output = tmux("list-windows", "-a", "-F", "#{window_id} #{window_zoomed_flag}", check=False)
    for entry in set(output.splitlines()):
        window, _, zoomed = entry.partition(" ")
        if window.startswith("@") and zoomed == "0":
            try:
                remember(window, current(window))
            except RuntimeError:  # A window can close while seeding.
                continue


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "seed-all":
        seed_all()
    elif len(sys.argv) == 3 and sys.argv[1] == "update" and re.fullmatch(r"@\d+", sys.argv[2]):
        update(sys.argv[2])
    else:
        raise SystemExit("usage: proportional.py seed-all | update @window-id")
