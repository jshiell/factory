from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxPolicy:
    """What a confined process is granted, as run today: the worktree read-only, `write` and `read` directories,
    the working directory (writable) and open network. `cwd` is the directory the process is launched in: nono ignores
    it (`--allow-cwd` grants the process cwd, not the profile), another sandbox must make it writable itself.
    `profile` is opaque: nono uses it, another sandbox may ignore it (and then grants too little, never too much)."""

    worktree: str | Path
    write: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    profile: str | None = None
    cwd: str | Path | None = None


class Sandbox(Protocol):
    def wrap(self, policy: SandboxPolicy) -> list[str]:
        """The argv prefix that runs the command after it under `policy`."""


class NonoSandbox:
    def __init__(self, executable: str | None = None):
        self._executable = "nono" if executable is None else executable

    def wrap(self, policy: SandboxPolicy) -> list[str]:
        """Without a profile `--allow-cwd` is read-only, so write access is granted via `--allow`."""
        prefix = [self._executable, "run", "-s"] + (["--profile", policy.profile] if policy.profile else [])
        prefix += ["--allow-cwd", "--read", str(Path(policy.worktree).resolve())]
        for directory in policy.write:
            prefix += ["--allow", directory]
        for directory in policy.read:
            prefix += ["--read", directory]
        return [*prefix, "--"]


def contains(directory: str, inner: str) -> bool:
    return Path(inner).resolve().is_relative_to(Path(directory).resolve())


def without_private(directories: Iterable[str], private_dirs: Iterable[str]) -> tuple[str, ...]:
    """The `directories` that do not contain any private directory (granting one would expose it)."""
    private = tuple(private_dirs)
    return tuple(d for d in directories if not any(contains(d, p) for p in private))


DEFAULT_SANDBOX = "nono"
SANDBOXES: dict[str, Callable[[str | None], Sandbox]] = {DEFAULT_SANDBOX: NonoSandbox}


def sandbox_named(name: str, executable: str | None = None) -> Sandbox:
    """`executable` is the `sandbox_executable` setting; `None` (unset) means the sandbox's own default."""
    return SANDBOXES[name](executable)
