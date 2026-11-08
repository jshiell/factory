"""Run the auditor over the labelled corpus (plan 22, promoted by plan 23)."""
import subprocess
from pathlib import Path

from factory.audit_corpus import BASE_FILES, PROJECT_SUBDIR, Case
from factory.gate import commit_all
from factory.worktrees import WorktreeManager, run_git

_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost", "-c", "commit.gpgsign=false")


def build_base_repo(root: Path) -> Path:
    repo = root / "base"
    for rel, content in BASE_FILES.items():
        path = repo / PROJECT_SUBDIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (repo / ".gitignore").write_text(BASE_FILES[".gitignore"])
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    run_git(repo, "add", "-A")
    run_git(repo, *_IDENTITY, "commit", "-q", "-m", "base")
    return repo


def materialise(case: Case, manager: WorktreeManager, task_id: str) -> tuple[Path, str]:
    """A fresh worktree with the case's after-state committed on top of the shared base."""
    worktree, base_sha = manager.create(task_id)
    for rel, content in case.files.items():
        path = worktree / PROJECT_SUBDIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commit_all(worktree, "change")
    return worktree, base_sha
