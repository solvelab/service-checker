"""Tests for the archive reminder.

The judgement being tested is "complete versus in progress". Getting it wrong in either
direction destroys the tool: too eager and every in-flight change nags, too lax and the
debt this exists to catch slips through again — it already slipped three times.

Everything runs against a temporary tree, never the repository's real `openspec/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_openspec_archive import read_tasks, report, scan  # noqa: E402


def _change(root: Path, name: str, tasks: str | None) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    if tasks is not None:
        (directory / "tasks.md").write_text(tasks, encoding="utf-8")
    return directory


DONE = "# Tasks\n- [x] 1.1 a\n- [x] 1.2 b\n"
PARTIAL = "# Tasks\n- [x] 1.1 a\n- [ ] 1.2 b\n"
NONE_DONE = "# Tasks\n- [ ] 1.1 a\n- [ ] 1.2 b\n"


# ---------------------------------------------------------------------------
# The core judgement
# ---------------------------------------------------------------------------

def test_a_change_with_every_task_done_is_complete(tmp_path):
    _change(tmp_path, "add-thing", DONE)
    assert scan(tmp_path)[0].complete is True


def test_a_partially_done_change_is_not_complete(tmp_path):
    _change(tmp_path, "add-thing", PARTIAL)
    assert scan(tmp_path)[0].complete is False


def test_a_change_with_nothing_done_is_not_complete(tmp_path):
    _change(tmp_path, "add-thing", NONE_DONE)
    assert scan(tmp_path)[0].complete is False


def test_counts_are_reported_accurately(tmp_path):
    _change(tmp_path, "add-thing", PARTIAL)
    status = scan(tmp_path)[0]
    assert (status.done, status.total) == (1, 2)


def test_an_uppercase_tick_counts_as_done(tmp_path):
    _change(tmp_path, "add-thing", "- [X] 1.1 a\n")
    assert scan(tmp_path)[0].complete is True


def test_indented_tasks_are_counted(tmp_path):
    _change(tmp_path, "add-thing", "## 1\n  - [x] 1.1 a\n  - [x] 1.2 b\n")
    assert scan(tmp_path)[0].done == 2


def test_prose_mentioning_brackets_is_not_a_task(tmp_path):
    _change(tmp_path, "add-thing", "- [x] real task\nsome prose [ ] not a box\n")
    assert scan(tmp_path)[0].total == 1


# ---------------------------------------------------------------------------
# The archive directory is not a change
# ---------------------------------------------------------------------------

def test_the_archive_directory_is_skipped(tmp_path):
    archive = tmp_path / "archive"
    _change(archive, "2026-01-01-old", DONE)
    assert scan(tmp_path) == []


def test_an_archive_alongside_a_live_change_reports_only_the_live_one(tmp_path):
    _change(tmp_path / "archive", "2026-01-01-old", DONE)
    _change(tmp_path, "add-thing", PARTIAL)
    assert [s.name for s in scan(tmp_path)] == ["add-thing"]


def test_only_the_archive_present_means_nothing_in_flight(tmp_path):
    (tmp_path / "archive").mkdir()
    assert scan(tmp_path) == []
    assert "no changes in flight" in report(scan(tmp_path))


# ---------------------------------------------------------------------------
# Malformed input must never crash
# ---------------------------------------------------------------------------

def test_a_change_without_tasks_md_does_not_crash(tmp_path):
    _change(tmp_path, "add-thing", None)
    status = scan(tmp_path)[0]
    assert status.complete is False
    assert status.note == "no tasks.md"


def test_a_tasks_file_with_no_checkboxes_is_not_complete(tmp_path):
    """Zero of zero is not done — it is unjudgeable."""
    _change(tmp_path, "add-thing", "# Tasks\njust prose, no boxes\n")
    status = scan(tmp_path)[0]
    assert status.complete is False
    assert status.note == "no task checkboxes"


def test_an_empty_tasks_file_is_not_complete(tmp_path):
    _change(tmp_path, "add-thing", "")
    assert scan(tmp_path)[0].complete is False


def test_undecodable_tasks_file_counts_as_no_tasks(tmp_path):
    directory = tmp_path / "add-thing"
    directory.mkdir()
    (directory / "tasks.md").write_bytes(b"\xff\xfe\x00binary")
    assert read_tasks(directory / "tasks.md") == (0, 0)


def test_a_stray_file_in_the_changes_directory_is_ignored(tmp_path):
    (tmp_path / "README.md").write_text("not a change", encoding="utf-8")
    assert scan(tmp_path) == []


def test_a_missing_changes_directory_is_not_an_error(tmp_path):
    assert scan(tmp_path / "does-not-exist") == []


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def test_the_report_names_a_complete_change(tmp_path):
    _change(tmp_path, "add-google-chat-notifier", DONE)
    text = report(scan(tmp_path))
    assert "COMPLETE" in text
    assert "add-google-chat-notifier" in text


def test_the_report_suggests_the_archive_command(tmp_path):
    _change(tmp_path, "add-thing", DONE)
    assert "openspec archive add-thing" in report(scan(tmp_path))


def test_the_report_says_it_is_a_reminder_not_a_failure(tmp_path):
    _change(tmp_path, "add-thing", DONE)
    text = report(scan(tmp_path))
    assert "not a failure" in text


def test_an_in_progress_change_is_listed_without_the_nag(tmp_path):
    _change(tmp_path, "add-thing", PARTIAL)
    text = report(scan(tmp_path))
    assert "in progress" in text
    assert "COMPLETE" not in text
    assert "archive" not in text


def test_complete_and_in_progress_are_both_shown(tmp_path):
    _change(tmp_path, "done-one", DONE)
    _change(tmp_path, "wip-one", PARTIAL)
    text = report(scan(tmp_path))
    assert "COMPLETE     done-one" in text
    assert "in progress  wip-one" in text


def test_the_count_of_complete_changes_is_stated(tmp_path):
    _change(tmp_path, "a", DONE)
    _change(tmp_path, "b", DONE)
    assert "2 change(s)" in report(scan(tmp_path))


def test_changes_are_reported_in_a_stable_order(tmp_path):
    for name in ("zebra", "alpha", "middle"):
        _change(tmp_path, name, PARTIAL)
    assert [s.name for s in scan(tmp_path)] == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# It must never block
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tasks", [DONE, PARTIAL, NONE_DONE, "", None])
def test_the_check_always_exits_zero(tmp_path, tasks, capsys):
    import check_openspec_archive as checker

    _change(tmp_path, "add-thing", tasks)
    original = sys.argv
    sys.argv = ["check_openspec_archive.py", str(tmp_path)]
    try:
        assert checker.main() == 0
    finally:
        sys.argv = original
    assert capsys.readouterr().out
