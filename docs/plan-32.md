# Plan 32: how much does the plan reviewer's required-note set vary run to run? (measure only)

Goal: the audit enforces "required" reviewer notes, so a required note that appears in one review of a plan and not in another changes whether a task can pass. Before any product change, measure how much the shipped review prompt's required notes vary across repeated runs.

## Decided by the user (not up for review)
- Measure first. No change to `review_plan.py`, the prompt or any product code in this plan; a follow-up plan (union, majority vote, rubric) is chosen from the numbers.
- The user runs the spike (real spend); I write and sanity-check it without spending, and record the results.
- Corpus: the toy calc plans from `spike/review_probe.py` (`mean`, `is_even`, `join_words`, `parse_ints`), run from the repo root as the probes did. No stored plans, no worktrees.
- 8 runs per plan, 4 plans (32 runs).
- Plus the standing rules: uv, pytest, stdlib only; never push; signed commits. The script is throwaway and flagged as such (TDD exempt).

## Facts (read from the code by me and by plan-reviewer; none run)
- The shipped prompt is `review_plan.build_review_prompt(goal, plan)`; the shipped config is `REVIEW_CONFIG` (sonnet, `budget_usd` 0.30, `max_turns` 8, `confine=True`, JSON schema, a read-only `append_system_prompt`, no `allowed_tools`). `spike/review_probe.py` does NOT use it: it passes `--allowedTools "Read Grep"`, omits the system prompt and runs unconfined. So the probe flags are not the shipped reviewer's.
- Each finding is one line ending `Recommend (required): ...` or `Recommend (optional): ...`. `review_notes.is_required` is a case-insensitive substring match on `recommend (required)`; the product sends `capped_findings(...)` to the agents (required first, 10 items, 500-character cut keeps the tag, `[truncated]` marker).
- The toy plans carry the input classes the review prompt asks about (bytes/str, nan, overflow, empty), so topic bucketing is meaningful for them. About $0.03 a run in plans 26 and 27 on these plans.

## Design (one throwaway script, `spike/review_variance.py`)
1. Corpus: `PLANS` copied (not imported, to freeze it) for `mean`, `is_even`, `join_words`, `parse_ints`.
2. Command: `factory.command.build_command(build_review_prompt(goal, plan), REVIEW_CONFIG)` (check its real signature first), run with the same env as the probe and cwd the repo root. If the nono confinement cannot be applied from a script, say so in the findings (unconfined is a known remaining difference); do not pass different tool flags.
3. Per run record: verdict, finding count, required, optional and untagged counts on the raw lines, the required count after `capped_findings`, exit status, `is_error`, cost. A run with no structured output, a non-zero exit or `is_error` is counted as a failed run, never as "no required note".
4. Topic bucketing (toy plans only): the probe's `HITS` regexes (bytes/str, overflow/inf, nan) plus empty, bool, negative/zero, other. A note's topic is its first match.
5. Report per plan: the required-count histogram; the fraction of approve runs with zero required notes (what the gate sees); the verdict split; failed runs; per topic, how many runs had it required, optional, or absent. The pure summary function is importable (no I/O) so it can be checked on fake data.
6. Print the raw required notes per run (truncated to 160 characters) so the human can judge the bucketing. Raw JSON of each run is kept under `spike/review-variance-out/` with a name that cannot collide (plan, run index, `time.time_ns()`).
7. Safety: runs are sequential, the running cost is printed, and the script aborts above a `--max-usd` limit (default 3.00). After the main pass, one plan is rerun once from a fresh process to show the spread is not an ordering effect.
8. Add `spike/review-variance-out/` to `.gitignore` in the C1 commit.

## Cycles
- C0: this plan, committed first.
- C1: write the script. Throwaway, no TDD. Sanity check, no spend: import the summary function and run it on a hand-made list of fake runs (including a failed run, an untagged line and a `[truncated]` marker), as a one-off `python -c`, not committed. Also check `build_command`'s signature and print the argv without running it.
- C2 (user runs): `uv run --project orchestrator python spike/review_variance.py`, 32 runs plus the rerun, about $1.20 expected, $3 abort.
- C3: record the numbers in `docs/spike-findings.md` (Plan 32), with what they do and do not show, and put the next step (union, vote, rubric or leave it) to the user.

## Risks
- n=8 per plan; the report gives counts, not rates, and no significance claim.
- Topic bucketing is regex and can mislabel; the raw notes are printed to check it.
- Unconfined run if confinement cannot be applied; the toy plans point at a `target/` project that may not exist in the repo root, which is also how the probes ran, so the comparison with plan 27 holds but it is not production's worktree.
- Real cost per run is unverified (the plan-27 figure is for the same plans); the abort bounds it.
- Toy plans say nothing about variance on real factory plans (residual).

## Residual
- Measures the shipped reviewer (sonnet, today) on four toy plans only; not real plans, and not whether any fix helps.
- Says nothing about whether a varying required note is right.
