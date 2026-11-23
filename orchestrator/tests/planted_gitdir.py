from pathlib import Path

from gitutil import git


def redirect_to_planted_gitdir(worktree: Path, tmp_path: Path, clone_of: Path) -> Path:
    """Rewrite the worktree's `.git` file to an agent-made clone of `clone_of` (same HEAD and history) whose clean filter,
    applied to every file, touches the returned marker."""
    marker = tmp_path / "planted-filter-ran"
    planted = tmp_path / "planted"
    git(tmp_path, "clone", "-q", str(clone_of), str(planted))
    git(planted, "config", "filter.evil.clean", f"touch {marker}; cat")
    (planted / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    (worktree / ".git").write_text(f"gitdir: {planted}/.git\n")
    return marker
