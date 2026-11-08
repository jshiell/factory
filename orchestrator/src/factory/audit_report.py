"""Read eval results, judge them against the labels, print the report (plan 22/23)."""
import json
import math
from pathlib import Path

from factory.audit_corpus import Case
from factory.textsafe import escape_control

EXIT_REGRESSION = 1
EXIT_INCOMPLETE = 3
VERDICTS = ("pass", "fail")


def load_records(eval_dir: Path) -> list[dict]:
    records = []
    for path in sorted(eval_dir.glob("case-*-r*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and "outcome" in record:
            records.append(record)
    return records


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _verdicts(records: list[dict]) -> dict[str, list[str]]:
    by_case: dict[str, list[str]] = {}
    for record in records:
        if record["outcome"] in VERDICTS:
            by_case.setdefault(record["case"], []).append(record["outcome"])
    return by_case


def exit_code(records: list[dict], runs: int, complete: bool, cases: list[Case] | None = None) -> int:
    """1 beats 3: a regression found in an incomplete run is still a regression."""
    verdicts = _verdicts(records)
    labels = {r["case"]: r["label"] for r in records}
    for case_id, label in labels.items():
        seen = verdicts.get(case_id, [])
        if label == "fail" and "pass" in seen:
            return EXIT_REGRESSION
        if label == "pass" and seen.count("fail") * 2 > len(seen):
            return EXIT_REGRESSION
    expected = [c.id for c in cases] if cases is not None else list(labels)
    needed = 1 if runs == 1 else 2
    if not complete or any(len(verdicts.get(case_id, [])) < needed for case_id in expected):
        return EXIT_INCOMPLETE
    return 0


def render(records: list[dict], cases: list[Case]) -> list[str]:
    by_case: dict[str, list[dict]] = {}
    for record in records:
        by_case.setdefault(record["case"], []).append(record)
    verdicts = _verdicts(records)
    lines = ["", "case     label category               verdicts (? is a run with no verdict)   first finding"]
    for case in cases:
        runs = sorted(by_case.get(case.id, []), key=lambda r: r["run"])
        shown = " ".join(r["outcome"] if r["outcome"] in VERDICTS else "?" for r in runs)
        first = next((r["findings"][0] for r in runs if r["outcome"] in VERDICTS and r["findings"]), "")
        lines.append(f"{case.id} {case.label:4} {case.category:22} {shown:26} {escape_control(first)[:70]}")
    for title, label, wrong in (("false-pass (bad judged pass)", "fail", "pass"), ("false-fail (good judged fail)", "pass", "fail")):
        eligible = [c for c in cases if c.label == label and verdicts.get(c.id)]
        majority = [c for c in eligible if verdicts[c.id].count(wrong) * 2 > len(verdicts[c.id])]
        anywhere = [c.id for c in eligible if wrong in verdicts[c.id]]
        low, high = wilson(len(majority), len(eligible))
        lines.append(f"\n{title}: majority wrong {len(majority)}/{len(eligible)} cases (Wilson 95% {low:.0%}-{high:.0%}); "
                     f"wrong in any run: {anywhere}")
    infra = [(r["case"], r["run"], r["outcome"]) for r in records if r["outcome"] not in VERDICTS]
    inconsistent = [c.id for c in cases if len(set(verdicts.get(c.id, []))) > 1]
    lines += [
        f"\nruns {len(records)}; without a verdict: {infra}",
        f"inconsistent cases: {inconsistent}",
        f"cost (real) ${sum(r['cost_usd'] for r in records):.3f}; assumed ${sum(r['assumed_usd'] for r in records):.2f}",
        "Intervals are over cases, not runs: runs of one case are correlated.",
    ]
    return lines
