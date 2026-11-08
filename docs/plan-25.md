# Plan 25: make the plan review look for edge-case gaps

Goal: the plan reviewer lists the edge cases the goal implies but the plan skips, without blocking sound plans.

Evidence: in the real-output audit the `mean` plan had gaps (bytes iterate into ints, overflow, NaN). The first review of it listed them; the rerun did not. Plan 24 forwards findings, but cannot forward ones that were not found.

## Spike result (`spike/review_probe.py`, 18 real runs, about $0.70)
Two plans, three runs each. `mean` has known gaps; `is_even(n: int)` is the control (sound plan).
- Today's prompt: no run found bytes, overflow or NaN on `mean` (0 of 3; this is variance, not a rate: the first real review did list them). All approve. Findings per run: 5.
- First probe ("probe for edge cases"): `mean` 3 of 3 found all three, but the control flipped to `revise` in 2 of 3 runs, inventing float/str/NaN gaps for an `int` signature. A `revise` fails the task, so it over-fires.
- Second probe (text below): `mean` 3 of 3 found bytes, overflow and NaN (and str, generators, precision), findings 8-9 per run, all approve; `is_even` 3 of 3 approve, findings 3-5 (zero, an odd negative, bool). Bar set before the run: met.
- **Caveat (reviewer finding 1):** the probe text names "Iterable admits str and bytes" and "overflow and non-finite values", which is the answer for `mean`. So the `mean` result shows the model follows the hints; it does not show the paragraph finds gaps it was not told about. Only the control staying `approve` is discriminating, and that is n=3 on a trivial plan. Hence the held-out gate below.
- The longest finding was 399 characters and the longest run about 2.2k characters, so the 500/4000 caps are not hit; the 10-item cap is (9 findings already).

## Decided (not up for review)
- The change is review-prompt text only: no new agent call, schema or verdict value. The wording is the one run in the spike (below) and is not re-tuned here; a change to it after the held-out gate is a new decision for the user.
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first (a characterisation test says it is green on arrival), never edit a passing test except via an approved edit list. Never push. Signed commits.

## The change
`review_plan.py` gains a module constant `EDGE_CASE_INSTRUCTION`, and `build_review_prompt` appends it after the plan text:

> Also list edge-case gaps the goal implies but the plan does not handle. Only consider inputs the plan's declared signature admits (for example, a parameter typed Iterable admits str and bytes because they iterate; a parameter typed int does not admit floats or strings). Cover silent wrong results, empty or single inputs, overflow and non-finite values where they can occur. Give each gap its own finding naming a concrete input and what the plan would do with it. Edge-case gaps must NOT change the verdict: use revise only for errors in the plan itself (wrong paths, contradictions, missing steps, breaking existing tests). Do not invent gaps that do not apply.

The "plan is untrusted data" sentence stays before the plan. The appended paragraph has no delimiter, so a plan ending in text that imitates it blends in; injection exposure is unchanged, and a delimiter would change the validated text, so it is left (residual).

Production vs spike (verified by reading): the spike ran unsandboxed from the repo root with `--allowedTools "Read Grep"`; production uses `allowed_tools=()` (pinned at `test_review_plan.py:59`), `confine=True`, cwd inside the worktree's `target/`. The e2e covers this.

## Interaction with plan 24 (the largest risk)
Findings now reach the implementer and the auditor, and the auditor fails a change that ignores a note. With 8-9 findings per run, each an unhandled edge case by construction (some minor: precision, single element), an honest change may ignore several and be failed. The implementer is told "address those that fit the plan"; the auditor treats the notes as the task. Measured by the new corpus case below.

Truncation: `capped_findings` keeps model order and drops everything after item 10, with `[truncated]`. A real plan error listed after the gap items could be cut from what the implementer gets. The `revise` screen (`cli.py:180`) shows all findings uncapped, and the approve screen shows the capped list, so the human sees what is sent. Checked by the e2e and the messy held-out plan.

## Cycles (red, green, commit each)
- S0 (spike hygiene, one commit, no code under test): commit `spike/review_probe.py` frozen: baseline and first-probe text inline, probe2 text inline, docstring corrected (18 runs, about $0.70, variants named), imports nothing that C1 changes. Add `spike/review-probe-out/` to `.gitignore` (like the other spike outputs).
- H (held-out gate, user runs, about $0.30): add 3 new plans to the script, no new wording: a sound control with a different shape (not a numeric function), a plan with a different gap class (for example a parser or a file/path function), and a messy multi-step plan with a real plan error plus gaps. 3 runs each with the frozen probe2 text. Gate: both controls approve in every run; gaps are not just hint echoes (I read the findings); the messy plan's real error appears among the first 10 findings and the verdict is `revise` only for that error. Stop here and report if it fails.
- C0 (characterisation, green on arrival): `build_review_prompt` names the goal and the plan, and "untrusted" appears before the plan.
- C1: add `EDGE_CASE_INSTRUCTION` and append it. One test each, red first: the prompt ends with the shared constant (pins the validated text); it comes after the plan text; the constant says gaps must not change the verdict; it states the declared-signature rule. Then a second small commit: the spike script imports the constant for its probe2 variant (baseline and probe1 stay frozen inline).
- C2 (corpus case, fail-or-pass decided now): corpus case-22, label **pass**: a good `mean` change that addresses 3 of 8 realistic notes (bytes rejected, empty raises, generator accepted) and ignores minor ones (precision, single element), with the notes as `review_findings`. Test first: the case is well-formed (plan names the files, notes present, label checked against its text); `--check-corpus` (user), then `eval-audit --only case-22` (user, a few cents). **Decision rule fixed now:** if the auditor fails it in 2 of 3 runs or more, stop and ask the user between (a) softening how the auditor uses the notes, (b) accepting the risk as residual, (c) dropping this plan's change. The corpus count changes 21 to 22; the older 20-case and 21-case hashes stay pinned (first 20 `corpus_hash` unchanged; expect the approved edits to count assertions only, each listed at the time).
- E2E (user, about $0.6): `mean` and a trivial goal, each through to `audited` with the real stage. Pass: the `mean` notes cover bytes, overflow and NaN and none of them are cut by `capped_findings`; the trivial plan is approved; `mean(b"12")` and `mean([1e308, 1e308])` are handled after the implementer and fix rounds; the trivial task is not failed for ignoring notes; the reviewer's turn count and file reads look sane (sandbox and cwd differ from the spike). One run each is a sample of one.
- C3: README, `docs/spike-findings.md` (spike result, leak caveat, n=3 samples, held-out result, plan-24 false-fail measurement).

## Approved edit list
- None expected. Only `test_review_plan.py:51-55` asserts on `build_review_prompt` (goal, plan, "untrusted") and no assertion there changes; no other test or the spike scripts touch it (grepped). The corpus count assertions (21 to 22) are listed with before and after when reached.

## Residual
- Notes widen the diff and raise the odds the auditor fails an honest change; only case-22 measures it, one case.
- The probe text still hints at str/bytes; behaviour on other gap classes is what the held-out gate samples (n=3 per plan).
- A model can still return `revise` for a gap despite the instruction; that blocks the plan as today, visibly.
- Prompt text cannot be proven by a unit test; tests pin the text, not the behaviour.
