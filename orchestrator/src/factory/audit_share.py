"""Turn an eval directory into something safe to hand to someone else (plan eval-export)."""
import hashlib
from pathlib import Path

KEPT = ("corpus_hash", "case_hashes", "cases", "runs", "complete", "prompt_hash")


def _name(directory: str) -> str:
    return Path(directory).name


def _unique_names(directories: list[str]) -> None:
    names = [_name(d) for d in directories]
    for name in names:
        if names.count(name) > 1:
            raise ValueError(f"two plugin directories are named {name}")


def shareable_meta(meta: dict) -> dict:
    """Meta without absolute paths or prompt text. Idempotent, so a bundle's meta and a directory's compare equal."""
    config = dict(meta.get("audit_config", {}))
    dirs = list(config.get("plugin_dirs", []))
    hashes = meta.get("plugin_hashes", {})
    _unique_names(dirs + [d for d in hashes if d not in dirs])
    config["plugin_dirs"] = [_name(d) for d in dirs]
    if "append_system_prompt" in config:
        prompt = config.pop("append_system_prompt")
        config["append_system_prompt_sha256"] = None if prompt is None else hashlib.sha256(prompt.encode()).hexdigest()[:16]
    shared = {key: meta[key] for key in KEPT if key in meta}
    shared["audit_config"] = config
    shared["plugin_hashes"] = {_name(directory): digest for directory, digest in hashes.items()}
    return shared
