"""Turn an eval directory into something safe to hand to someone else (plan eval-export)."""
import hashlib
import json
import re
from pathlib import Path

from factory.audit_corpus import Case, corpus_hash
from factory.audit_eval import _write_atomically
from factory.audit_report import exit_code, load_records, summary
from factory.harness import DEFAULT_HARNESS
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
    shared["harness"] = meta.get("harness", DEFAULT_HARNESS)
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


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _fence(text: str) -> str:
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def _percent(low_high: list[float]) -> str:
    return f"{low_high[0]:.0%}-{low_high[1]:.0%}"


def render_markdown(bundle: dict) -> str:
    """The bundle as Markdown. Reads only the bundle, so it is deterministic and cannot show more than the JSON does."""
    meta, totals = bundle["meta"], bundle["summary"]
    by_case: dict[str, list[dict]] = {}
    for record in bundle["records"]:
        by_case.setdefault(record["case"], []).append(record)
    labels = {record["case"]: record for record in bundle["records"]}
    lines = [
        f"# Auditor eval {bundle['eval']}", "",
        f"- corpus {meta['corpus_hash']}, audit prompt {meta['prompt_hash']}, model {meta['audit_config'].get('model')}, "
        f"harness {meta['harness']}",
        f"- {meta['runs']} runs per case, {'complete' if meta['complete'] else 'incomplete'}, exit code {bundle['exit_code']}",
        "", "| case | label | category | verdicts (? is a run with no verdict) |", "| --- | --- | --- | --- |",
    ]
    for case_id in meta["cases"]:
        runs = sorted(by_case.get(case_id, []), key=lambda r: r["run"])
        shown = " ".join(r["outcome"] if r["outcome"] in ("pass", "fail") else "?" for r in runs)
        head = labels.get(case_id, {})
        lines.append(f"| {_cell(case_id)} | {head.get('label', '')} | {_cell(head.get('category', ''))} | {shown} |")
    lines.append("")
    for title, key in (("false-pass (bad judged pass)", "false_pass"), ("false-fail (good judged fail)", "false_fail")):
        part = totals[key]
        lines.append(f"- {title}: majority wrong {len(part['majority'])}/{part['eligible']} cases "
                     f"(Wilson 95% {_percent(part['wilson'])}); wrong in any run: {part['any_run']}")
    lines += [
        f"- runs {totals['runs']}; without a verdict: {[tuple(entry) for entry in totals['infra']]}",
        f"- inconsistent cases: {totals['inconsistent']}",
        f"- cost (real) ${totals['cost_usd']:.3f}; assumed ${totals['assumed_usd']:.2f}",
        "", "Intervals are over cases, not runs: runs of one case are correlated.",
    ]
    with_findings = [r for r in bundle["records"] if r.get("findings")]
    if with_findings:
        lines += ["", "## Findings"]
        for record in with_findings:
            lines += ["", f"{_cell(record['case'])} run {record['run']} ({record['outcome']}):"]
            lines += [_fence(finding) for finding in record["findings"]]
    return "\n".join(lines) + "\n"


def write_bundle(bundle: dict, directory: Path) -> tuple[Path, Path]:
    """Write run.json and run.md into an existing directory, replacing earlier ones."""
    json_path, markdown_path = directory / "run.json", directory / "run.md"
    _write_atomically(json_path, json.dumps(bundle, indent=1) + "\n")
    _write_atomically(markdown_path, render_markdown(bundle))
    return json_path, markdown_path
