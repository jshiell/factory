import os
import tempfile
from pathlib import Path

from factory.worktrees import WorktreeGit


def worktree_fingerprint(tree: WorktreeGit) -> tuple[str, str, str]:
    """HEAD, `status --porcelain`, and the tree of everything not ignored (untracked files included).

    The tree is written from a temporary index, so the worktree's own index is untouched.
    Ignored files are outside it."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        tree.git("add", "-A", env=env)
        everything = tree.git("write-tree", env=env)
    return tree.git("rev-parse", "HEAD"), tree.git("status", "--porcelain"), everything
