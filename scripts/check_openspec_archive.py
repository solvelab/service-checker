"""Report OpenSpec changes whose tasks are all done but that were never archived.

Stage 3 of `openspec/AGENTS.md` says archiving a delivered change is a separate PR,
after the deploy. In practice nobody remembers, and the debt became recurring: issues
#1, #17 and #29 archived five changes between them, always found by someone looking,
never by a check.

While a delivered change sits in `openspec/changes/`, `openspec list` misrepresents the
project — the in-flight list includes work already in production — and the requirements
it adds never reach `openspec/specs/`.

This **warns**; it does not fail. A change can legitimately have every task ticked and
not be deployed yet, and blocking on that would punish the honest case.

    python scripts/check_openspec_archive.py [openspec/changes]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional

# Matches a markdown task box at the start of a line, ticked or not.
_TASK = re.compile(r"^\s*-\s*\[( |x|X)\]\s", re.M)


class ChangeStatus(NamedTuple):
    name: str
    done: int
    total: int
    note: Optional[str] = None

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done == self.total


def read_tasks(tasks_file: Path) -> tuple[int, int]:
    """Count (done, total) task boxes. Unreadable content counts as no tasks."""
    try:
        text = tasks_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0, 0
    boxes = _TASK.findall(text)
    done = sum(1 for box in boxes if box in ("x", "X"))
    return done, len(boxes)


def scan(changes_dir: Path) -> List[ChangeStatus]:
    """One entry per change directory, excluding the archive itself."""
    if not changes_dir.is_dir():
        return []
    statuses: List[ChangeStatus] = []
    for entry in sorted(changes_dir.iterdir()):
        if not entry.is_dir() or entry.name == "archive":
            continue
        tasks_file = entry / "tasks.md"
        if not tasks_file.is_file():
            # A change without tasks.md cannot be judged complete; say so rather
            # than guess, and never crash on it.
            statuses.append(ChangeStatus(entry.name, 0, 0, "no tasks.md"))
            continue
        done, total = read_tasks(tasks_file)
        note = "no task checkboxes" if total == 0 else None
        statuses.append(ChangeStatus(entry.name, done, total, note))
    return statuses


def report(statuses: List[ChangeStatus]) -> str:
    if not statuses:
        return "openspec: no changes in flight"

    lines = []
    complete = [s for s in statuses if s.complete]
    in_progress = [s for s in statuses if not s.complete]

    for status in in_progress:
        detail = status.note or f"{status.done}/{status.total} tasks"
        lines.append(f"  in progress  {status.name}  ({detail})")
    for status in complete:
        lines.append(f"  COMPLETE     {status.name}  ({status.done}/{status.total} tasks)")

    if complete:
        names = " ".join(s.name for s in complete)
        lines.append("")
        lines.append(
            f"{len(complete)} change(s) with every task done are still in flight."
        )
        lines.append("If they are deployed, archive them in their own PR:")
        lines.append(f"  openspec archive {names.split()[0]}")
        lines.append("This is a reminder, not a failure — a finished change may not be")
        lines.append("deployed yet.")
    return "\n".join(lines)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openspec/changes")
    print(report(scan(root)))
    return 0  # always: this warns, it never blocks


if __name__ == "__main__":
    raise SystemExit(main())
