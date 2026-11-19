"""Turn an eval directory into something safe to hand to someone else (plan eval-export)."""
import hashlib
import json
from pathlib import Path

from factory.audit_corpus import Case, corpus_hash
from factory.audit_report import exit_code, load_records, summary
from factory.textsafe import escape_control

SCHEMA = 1
RECORD_KEYS = ("case", "run", "label", "category", "outcome", "cost_usd", "assumed_usd")

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


def _meta_of(eval_dir: Path) -> dict:
    try:
        meta = json.loads((eval_dir / "meta.json").read_text())
    except (OSError, ValueError):
        raise ValueError(f"{eval_dir.name} has no readable meta.json") from None
    if not isinstance(meta, dict):
        raise ValueError(f"{eval_dir.name} has no readable meta.json")
    return meta


def _shared_record(record: dict, include_findings: bool) -> dict:
    shared = {key: record[key] for key in RECORD_KEYS}
    if include_findings:
        shared["findings"] = [escape_control(finding) for finding in record["findings"]]
    return shared


def build_bundle(eval_dir: Path, corpus: list[Case], include_findings: bool = False) -> dict:
    """Everything needed to read or compare a run, with no paths, no prompt text and (unless asked) no findings."""
    meta = _meta_of(eval_dir)
    cases = [case for case in corpus if case.id in meta.get("cases", [])]
    if [c.id for c in cases] != meta.get("cases") or corpus_hash(cases) != meta.get("corpus_hash"):
        raise ValueError("the corpus changed since this run: its labels cannot be trusted")
    records = load_records(eval_dir)
    return {
        "schema": SCHEMA, "eval": eval_dir.name, "meta": shareable_meta(meta),
        "records": [_shared_record(record, include_findings) for record in records],
        "summary": summary(records, cases),
        "exit_code": exit_code(records, meta["runs"], meta["complete"], cases),
    }
