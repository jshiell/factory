import os

import pytest

from factory.gate_cache import cache_size, orphan_caches
from factory.store import TaskStore
from factory.task import Task, TaskState


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "gate-cache").mkdir()
    return tmp_path / "gate-cache"


def test_the_size_sums_files_without_following_symlinks(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_text("x" * 10_000)
    entry = root / "t1"
    (entry / "sub").mkdir(parents=True)
    (entry / "a").write_text("12345")
    (entry / "sub" / "b").write_text("123")
    os.symlink(outside, entry / "link")

    size = cache_size(root, "t1")

    assert 8 <= size < 5_000


def test_the_size_of_a_missing_dir_a_symlink_or_a_plain_file_is_none(root, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", root / "linked")
    (root / "plain").write_text("x")

    assert [cache_size(root, name) for name in ("nope", "linked", "plain")] == [None, None, None]


def test_a_missing_cache_root_has_no_orphans_and_nothing_skipped(store, tmp_path):
    scan = orphan_caches(store, tmp_path / "no-such-dir")

    assert scan.orphans == [] and scan.skipped == []


def test_a_directory_with_no_task_record_is_an_orphan_with_its_size(store, root):
    (root / "gone").mkdir()
    (root / "gone" / "f").write_text("abc")

    scan = orphan_caches(store, root)

    assert [(o.name, o.size > 0) for o in scan.orphans] == [("gone", True)] and scan.skipped == []


@pytest.mark.parametrize("state", list(TaskState))
def test_the_cache_of_a_task_with_a_record_is_never_an_orphan(store, root, state):
    store.save(Task(id="t1", prompt="", state=state))
    (root / "t1").mkdir()

    assert orphan_caches(store, root).orphans == []


def test_symlinks_plain_files_and_unsafe_names_are_skipped_and_reported(store, root, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", root / "linked")
    (root / "plain").write_text("x")
    (root / "bad name!").mkdir()

    scan = orphan_caches(store, root)

    assert scan.orphans == []
    assert sorted(scan.skipped) == ["bad name!", "linked", "plain"]


def test_a_cache_whose_task_record_is_corrupt_is_kept_and_reported_not_an_orphan(store, root, tmp_path):
    (tmp_path / "tasks" / "t1.json").write_text("{nope")
    (root / "t1").mkdir()

    scan = orphan_caches(store, root)

    assert scan.orphans == [] and scan.skipped == ["t1"]
