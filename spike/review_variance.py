"""THROWAWAY (plan 32 spike). How much does the shipped reviewer's required-note set vary run to run?
Measure only: no product change. Toy plans are frozen copies from spike/review_probe.py.

Run: uv run --project orchestrator python spike/review_variance.py [--runs 8] [--plans mean,is_even,join_words,parse_ints]
                                                                     [--max-usd 3.00] [--no-confine] [--dry-run]
Uses build_command(build_review_prompt(...), REVIEW_CONFIG) like the product, wrapped in nono (as SubprocessRunner does)
unless --no-confine. Real API calls (about $0.03 a run). Output: spike/review-variance-out/ (gitignored)."""
import argparse, json, os, re, subprocess, sys, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "review-variance-out"
sys.path.insert(0, str(ROOT / "orchestrator" / "src"))
from factory.command import build_command, sandbox_prefix  # noqa: E402
from factory.review_notes import capped_findings, is_required  # noqa: E402
from factory.review_plan import REVIEW_CONFIG, build_review_prompt  # noqa: E402
from factory.runner import STRIPPED_ENV  # noqa: E402

PLANS = {  # frozen copies of spike/review_probe.py PLANS
    "mean": ("Add a mean function to calc",
             "1. Add `mean(values: Iterable[float]) -> float` to `target/src/calc/__init__.py` (arithmetic mean; "
             "raise `ValueError` on an empty input).\n2. Add tests in a new file `target/tests/test_mean.py`."),
    "is_even": ("Add an is_even function to calc",
                "1. Add `is_even(n: int) -> bool` to `target/src/calc/__init__.py`.\n"
                "2. Add tests in a new file `target/tests/test_even.py` for an even, an odd and a negative number."),
    "join_words": ("Add a join_words function to calc",
                   "1. Add `join_words(words: list[str]) -> str` to `target/src/calc/__init__.py`, returning the words "
                   "joined by a single space (an empty list gives an empty string).\n"
                   "2. Add tests in a new file `target/tests/test_join_words.py` for several words, one word and an empty list."),
    "parse_ints": ("Add a function that parses a comma-separated string of integers",
                   "1. Add `parse_ints(text: str) -> list[int]` to `target/src/calc/__init__.py`: split `text` on commas "
                   "and convert each part with `int()`.\n"
                   "2. Add tests in a new file `target/tests/test_parse_ints.py` for \"1,2,3\"."),
}
TOPICS = [("bytes/str", r"bytes|\bstr\b|string"), ("overflow/inf", r"overflow|\binf\b|infinit"), ("nan", r"\bnan\b"),
          ("empty", r"\bempty\b|single"), ("bool", r"\bbool"), ("negative/zero", r"negative|\bzero\b"), ]


def topic(note):
    low = note.lower()
    return next((name for name, rx in TOPICS if re.search(rx, low)), "other")


def is_optional(line):
    return "recommend (optional)" in line.lower()


def run_record(output, cost, failed):
    """One run's numbers from its structured output (None when the run failed)."""
    if failed or output is None:
        return {"failed": True, "cost": cost}
    findings = list(output["findings"])
    required = [f for f in findings if is_required(f)]
    optional = [f for f in findings if is_optional(f) and not is_required(f)]
    return {"failed": False, "cost": cost, "verdict": output["verdict"], "findings": len(findings),
            "required": len(required), "optional": len(optional),
            "untagged": len(findings) - len(required) - len(optional),
            "required_after_cap": sum(is_required(f) for f in capped_findings(tuple(findings))),
            "required_notes": required, "topics_required": sorted({topic(f) for f in required}),
            "topics_optional": sorted({topic(f) for f in optional})}


def summarize(records):
    """Per-plan summary of run_record dicts. Pure."""
    ok = [r for r in records if not r["failed"]]
    approve = [r for r in ok if r["verdict"] == "approve"]
    topics = {}
    for name in sorted({t for r in ok for t in r["topics_required"] + r["topics_optional"]}):
        topics[name] = {"required": sum(name in r["topics_required"] for r in ok),
                        "optional_only": sum(name in r["topics_optional"] and name not in r["topics_required"] for r in ok),
                        "absent": sum(name not in r["topics_required"] + r["topics_optional"] for r in ok)}
    return {"runs": len(records), "failed": len(records) - len(ok),
            "verdicts": dict(Counter(r["verdict"] for r in ok)),
            "required_histogram": dict(sorted(Counter(r["required"] for r in ok).items())),
            "required_after_cap_histogram": dict(sorted(Counter(r["required_after_cap"] for r in ok).items())),
            "zero_required_among_approve": f"{sum(r['required'] == 0 for r in approve)}/{len(approve)}",
            "untagged_findings": sum(r["untagged"] for r in ok), "topics": topics}


def argv_for(goal, plan, confine):
    argv, overlay = build_command(build_review_prompt(goal, plan), REVIEW_CONFIG,
                                  add_dirs=(str(ROOT),) if confine else ())
    if confine:
        argv = sandbox_prefix("nono", ROOT) + argv
    env = {**{k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}, **overlay}
    return argv, env


def one_run(plan_name, i, confine):
    goal, plan = PLANS[plan_name]
    argv, env = argv_for(goal, plan, confine)
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    (OUT / f"{plan_name}-{i}-{time.time_ns()}.json").write_text(p.stdout)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return run_record(None, 0, True)
    failed = p.returncode != 0 or d.get("is_error") or not d.get("structured_output")
    return run_record(d.get("structured_output"), d.get("total_cost_usd") or 0, bool(failed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--plans", default=",".join(PLANS))
    ap.add_argument("--max-usd", type=float, default=3.0)
    ap.add_argument("--no-confine", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the argv for the first plan and exit")
    a = ap.parse_args()
    confine = REVIEW_CONFIG.confine and not a.no_confine
    names = a.plans.split(",")
    if a.dry_run:
        argv, _ = argv_for(*PLANS[names[0]], confine)
        print(json.dumps([x if len(x) < 120 else x[:117] + "..." for x in argv], indent=1)); return
    OUT.mkdir(exist_ok=True)
    print(f"confined={confine} (unconfined is a difference from production)" if not confine else "confined=True")
    total, results = 0.0, {}
    for name in names:
        recs = []
        for i in range(a.runs):
            if total > a.max_usd:
                print(f"ABORT: cost ${total:.2f} above --max-usd {a.max_usd}"); break
            r = one_run(name, i, confine); total += r["cost"]; recs.append(r)
            print(f"{name:10} #{i}: " + ("FAILED" if r["failed"] else
                  f"{r['verdict']:7} required={r['required']} (after cap {r['required_after_cap']}) optional={r['optional']} "
                  f"untagged={r['untagged']} req_topics={r['topics_required']}") + f"  ${total:.2f}")
            for n in r.get("required_notes", []):
                print(f"      - {n[:160]}")
        results[name] = recs
    if names and total <= a.max_usd:  # ordering check: rerun the first plan from a fresh process
        again = subprocess.run([sys.executable, __file__, "--plans", names[0], "--runs", "3", "--max-usd", str(a.max_usd - total)]
                               + (["--no-confine"] if a.no_confine else []), capture_output=True, text=True)
        print("\nFRESH-PROCESS RERUN (3 runs of", names[0] + "):\n" + again.stdout[-1500:])
    print("\nSUMMARY")
    for name, recs in results.items():
        print(name, json.dumps(summarize(recs), indent=1))
    print(f"total ${total:.2f}")


if __name__ == "__main__":
    main()
