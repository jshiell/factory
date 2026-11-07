import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from factory.ids import InvalidTaskId, require_safe_id
from factory.store import TaskNotFound, TaskStore


@dataclass(frozen=True)
class OrphanCache:
    name: str
    size: int


@dataclass
class OrphanScan:
    orphans: list[OrphanCache] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _real_directory(cache_root: Path, name: str) -> Path | None:
    """The cache dir `cache_root/name` if it is a real directory (never a symlink), else None."""
    path = cache_root / name
    try:
        return path if stat.S_ISDIR(os.lstat(path).st_mode) else None
    except OSError:
        return None


def cache_size(cache_root: Path, name: str) -> int | None:
    """Bytes under `cache_root/name`, not following symlinks; None when it is not a real directory."""
    directory = _real_directory(cache_root, name)
    if directory is None:
        return None
    total = 0
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        for entry in dirnames + filenames:
            try:
                total += os.lstat(os.path.join(current, entry)).st_size
            except OSError:
                pass
    return total


def orphan_caches(store: TaskStore, cache_root: Path) -> OrphanScan:
    """Entries of `cache_root` that no task record claims; anything doubtful is skipped and reported."""
    scan = OrphanScan()
    if not cache_root.is_dir():
        return scan
    for entry in sorted(os.listdir(cache_root)):
        try:
            require_safe_id(entry)
            size = cache_size(cache_root, entry)
            if size is None:
                scan.skipped.append(entry)
                continue
            store.load(entry)
        except TaskNotFound:
            scan.orphans.append(OrphanCache(entry, size))
        except InvalidTaskId:
            scan.skipped.append(entry)
        except Exception:  # a corrupt or newer-schema record: the cache may be live
            scan.skipped.append(entry)
    return scan


def remove_cache(cache_root: Path, name: str) -> str | None:
    """Remove `cache_root/name` if it is still a real directory; returns a problem message or None."""
    directory = _real_directory(cache_root, name)
    if directory is None:
        return f"cache {name} kept: it is no longer a plain directory"
    try:
        shutil.rmtree(directory)
    except OSError as error:
        return f"cache {name} partially removed: {error}"
    return None
