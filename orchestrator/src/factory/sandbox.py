from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxPolicy:
    """What a confined process is granted, as run today: the worktree read-only, `write` and `read` directories,
    the working directory (writable only if `profile` grants it) and open network. `profile` is opaque: nono uses
    it, another sandbox may ignore it (and then grants too little, never too much)."""

    worktree: str | Path
    write: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    profile: str | None = None


class Sandbox(Protocol):
    def wrap(self, policy: SandboxPolicy) -> list[str]:
        """The argv prefix that runs the command after it under `policy`."""


class NonoSandbox:
    def __init__(self, executable: str):
        self._executable = executable

    def wrap(self, policy: SandboxPolicy) -> list[str]:
        """Without a profile `--allow-cwd` is read-only, so write access is granted via `--allow`."""
        prefix = [self._executable, "run", "-s"] + (["--profile", policy.profile] if policy.profile else [])
        prefix += ["--allow-cwd", "--read", str(Path(policy.worktree).resolve())]
        for directory in policy.write:
            prefix += ["--allow", directory]
        for directory in policy.read:
            prefix += ["--read", directory]
        return [*prefix, "--"]
