"""Compare an eval with an earlier one, only where the comparison means something (plan 23)."""
import json
from pathlib import Path

from factory.audit_report import VERDICTS, load_records


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


def _verdict_lists(eval_dir: Path) -> dict[str, list[str]]:
    by_case: dict[str, list[tuple[int, str]]] = {}
    for record in load_records(eval_dir):
        by_case.setdefault(record["case"], []).append((record["run"], record["outcome"] if record["outcome"] in VERDICTS else "?"))
    return {case: [outcome for _, outcome in sorted(runs)] for case, runs in by_case.items()}


def _cost(eval_dir: Path) -> float:
    return sum(record["cost_usd"] for record in load_records(eval_dir))


def compare(old: Path, new: Path) -> list[str]:
    old_meta, new_meta = _meta(old), _meta(new) or {}
    lines = [f"\ncompared with {old.name}:"]
    if old_meta is None:
        lines.append("  no meta.json there: cases compared by id, not by content (the corpus may differ)")
    else:
        if old_meta.get("audit_config") != new_meta.get("audit_config"):
            lines.append("  AUDIT_CONFIG differs")
        if old_meta.get("prompt_hash") != new_meta.get("prompt_hash"):
            lines.append("  audit prompt differs")
    old_runs, new_runs = _verdict_lists(old), _verdict_lists(new)
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
    lines.append(f"  cost ${_cost(old):.2f} -> ${_cost(new):.2f}")
    return lines
