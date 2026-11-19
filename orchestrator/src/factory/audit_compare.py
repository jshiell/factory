"""Compare an eval with an earlier one, only where the comparison means something (plan 23)."""
import json
from dataclasses import dataclass
from pathlib import Path

from factory.audit_report import VERDICTS, load_records
from factory.audit_share import shareable_meta


def _meta(eval_dir: Path) -> dict | None:
    try:
        meta = json.loads((eval_dir / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def find_previous(evals_root: Path, current: Path, case_ids: list[str]) -> Path | None:
    """Latest earlier directory that finished and covered every case (not a smoke run or a stopped one)."""
    if not evals_root.is_dir():
        return None
    for directory in sorted((d for d in evals_root.iterdir() if d.is_dir()), reverse=True):
        meta = _meta(directory)
        if directory != current and meta and meta.get("complete") is True and meta.get("cases") == case_ids:
            return directory
    return None


@dataclass(frozen=True)
class Run:
    """One eval as compare sees it, loaded from a directory or from an exported bundle."""
    name: str
    meta: dict | None  # shareable form; None for a directory with no meta.json
    verdicts: dict[str, list[str]]
    cost: float


def _verdict_lists(records: list[dict]) -> dict[str, list[str]]:
    by_case: dict[str, list[tuple[int, str]]] = {}
    for record in records:
        by_case.setdefault(record["case"], []).append((record["run"], record["outcome"] if record["outcome"] in VERDICTS else "?"))
    return {case: [outcome for _, outcome in sorted(runs)] for case, runs in by_case.items()}


def _bundle_run(path: Path) -> Run:
    try:
        bundle = json.loads(path.read_text())
        if bundle["schema"] != 1:
            raise ValueError(f"{path.name} has schema {bundle['schema']}, expected 1")
        meta = shareable_meta(bundle["meta"])
        records = bundle["records"]
        return Run(str(bundle["eval"]), meta, _verdict_lists(records), sum(record["cost_usd"] for record in records))
    except (OSError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"{path.name} is not an exported eval bundle ({type(error).__name__})") from None
    except json.JSONDecodeError:
        raise ValueError(f"{path.name} is not an exported eval bundle (invalid JSON)") from None


def load_run(source: Path) -> Run:
    """An eval directory, or a run.json written by eval-export. Raises ValueError for anything else."""
    if source.is_file():
        return _bundle_run(source)
    meta = _meta(source)
    records = load_records(source)
    return Run(source.name, shareable_meta(meta) if meta is not None else None, _verdict_lists(records),
               sum(record["cost_usd"] for record in records))


def compare(old: Path, new: Path) -> list[str]:
    old_run, new_run = load_run(old), load_run(new)
    old_meta, new_meta = old_run.meta, new_run.meta or {}
    lines = [f"\ncompared with {old_run.name}:"]
    if old_meta is None:
        lines.append("  no meta.json there: cases compared by id, not by content (the corpus may differ)")
    else:
        if old_meta.get("audit_config") != new_meta.get("audit_config"):
            lines.append("  AUDIT_CONFIG differs")
        if old_meta.get("plugin_hashes", {}) != new_meta.get("plugin_hashes", {}):
            lines.append("  audit plugins differ")
        if old_meta.get("prompt_hash") != new_meta.get("prompt_hash"):
            lines.append("  audit prompt differs")
    old_runs, new_runs = old_run.verdicts, new_run.verdicts
    changes, skipped = [], []
    for case_id, now in new_runs.items():
        if case_id not in old_runs:
            continue
        same_content = old_meta is None or old_meta["case_hashes"].get(case_id) == new_meta["case_hashes"].get(case_id)
        if not same_content:
            skipped.append(f"  {case_id}: not compared (the case content differs)")
        elif old_runs[case_id] != now:
            changes.append(f"  {case_id}: {' '.join(old_runs[case_id])} -> {' '.join(now)}")
    lines += skipped
    lines += ["verdicts changed:", *changes] if changes else ["  no verdict changed"]
    lines.append(f"  cost ${old_run.cost:.2f} -> ${new_run.cost:.2f}")
    return lines
