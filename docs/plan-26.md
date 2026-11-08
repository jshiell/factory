# Plan 26: reviewer gaps end with a recommended behaviour

Goal: the edge-case gaps the plan review lists (plan 25) become imperative notes, so the implementer is more likely to act on them, without blocking sound plans or failing honest changes.

Evidence: in the plan-25 real `mean` run the implementer only documented "str/bytes unsupported" and the auditor passed it. Case-21's imperative note ("Reject bytes with TypeError and add a test") was acted on, or its omission failed. Untested explanation: descriptive notes are optional, imperative ones are acted on.

## Spike result (`spike/review_probe.py`, 24 real runs, about $0.90)
Four plans (`mean`, `is_even`, `join_words`, `messy`), three runs each, per round. Bar set before each round.
- Round 1 (`probe3` = the shipped edge-case text + "End each edge-case gap finding with 'Recommend: ' + one imperative sentence"): verdicts all right (controls approve 9/9, `messy` revise 3/3 with the real errors first, `mean` approve 3/3 with bytes, overflow, NaN). Every gap finding carried "Recommend:". One finding was 559 characters, over the 500 cap. Bar missed on that point.
- Round 2 (`probe4` = probe3 + "Keep every finding under 400 characters."): same verdict results; longest finding 317; none over 500; every gap carried "Recommend:"; `mean` 3/3 found bytes, overflow, NaN. Bar met.
- Caps checked (reviewer finding 7): `capped_findings` over the 12 probe4 outputs: no run reaches 10 items, largest total about 1.5k of 4000, no per-item cut.
- Caveats: n=3 per cell; the `mean` result still partly echoes hints already in the text (plan 25); `join_words` gaps (empty and whitespace elements) were not hinted. Recommendations are opinionated and some are debatable ("use `math.fsum`", "raise ValueError for NaN").
- This measures only what the reviewer writes. Whether the implementer acts and the auditor stays fair is measured below.

## Decided (not up for review)
- Prompt text only: the two probe4 sentences are appended to `EDGE_CASE_INSTRUCTION`. No new schema, verdict, agent call or code path. Wording is the round-2 text, not re-tuned; a later change is a new decision for the user.
- **Policy (user chose it): reviewer notes are advisory.** An honest change may skip a note, including a substantive one (overflow, NaN), and the auditor should not fail it for that alone; it fails a change that is wrong or off-task. Case-23's "pass" label encodes this. `NOTES_HEADING` ("address those that fit the plan") stays as is.
- Consequence, stated plainly: imperative notes make the implementer likelier to act, but the auditor still will not demand it. If case-23 passes (the wanted outcome), whether `mean(b"12")` gets rejected in the E2E rests on the implementer alone.
- uv, pytest, stdlib only. TDD, one red-green-commit per behaviour, failing test shown red first (a characterisation test says it is green on arrival), never edit a passing test except via an approved edit list. Never push. Signed commits (unsigned if signing fails, re-sign at the end).

## The change
`review_plan.EDGE_CASE_INSTRUCTION` gains, at its end (after the verdict rule):

> End each edge-case gap finding with 'Recommend: ' followed by one imperative sentence stating the behaviour the implementation should have (for example, 'Recommend: raise TypeError for bytes and add a test'). Keep every finding under 400 characters.

Nothing else in `review_plan.py`, `review_notes.py`, the implementer or the auditor changes.

## Risks
- The auditor treats notes as the task, so imperative notes raise the odds it fails an honest change that skips some. Only case-23 measures this, one case.
- The 400-character limit is model compliance only; `MAX_ITEM=500` still slices silently with no per-item marker (round 1 showed a 559). The checked outputs are well inside the caps.
- A bad recommendation (for example `math.fsum` for a precision gap) is now an instruction the implementer may follow.

## Cycles (red, green, commit each)
- S0 (one commit, no code under test): commit `spike/review_probe.py` with `PROBE2` frozen inline (byte-identical to the current `EDGE_CASE_INSTRUCTION`, checked once with an equality run before committing), `PROBE3` and `PROBE4` inline, the `recommend` and `longest` columns, and the docstring updated (variants through probe4, 24 runs, about $0.90). It must not import the constant yet: at S0 the constant is still the old text, so an import would make probe4 equal probe2.
- C1: tests red first, pinning the exact new suffix as a literal (the "Recommend: " sentence and the 400-character clause), and that it comes after the verdict rule. Then extend the constant. Existing tests at `test_review_plan.py` (ends with the constant; "must NOT change the verdict"; "declared signature admits") stay green: they compare to the constant or to substrings that remain.
- S0b: a separate small commit: the spike's `PROBE4` becomes the imported constant, with `assert PROBE4 == EDGE_CASE_INSTRUCTION` (proves the shipped wording is the measured wording). Baseline, probe1 to probe3 and the frozen `PROBE2` stay inline.
- C2 (corpus case-23): the same goal, plan and files as case-22 (the change addresses bytes and str rejection and the generator, skips non-finite, overflow and precision), and `review_findings` = the 6 findings of `spike/review-probe-out/mean-probe4-0-1794151795.json`, copied verbatim into `audit_corpus.py` (that directory is gitignored, so the file name is recorded here and in a code comment). Label **pass** under the advisory policy. Tests first: well-formed; goal, plan and files equal case-22's; exactly 6 notes; every gap note contains "Recommend:" and every note is under 400 characters; the label is checked against its text. Edit list (with before and after, approved before any edit): `audit_corpus.py:258` (`== 22` to 23); `test_audit_corpus.py:7` (`== 22` twice to 23) and `:9` (pass count 8 to 9); `test_cli.py:1063` (`== 22` to 23), `:1171` (`len(calls) == 22` to 23), `:1173` ("22 cases, none blocked" to "23 cases, none blocked"). The first-20 hash pin (`2449f1ff319eeef3`, `test_audit_corpus.py:99`) is unaffected.
  - Then `--check-corpus` (user), `eval-audit --only case-23` (3 runs), `--only case-22` and `--only case-21` once each (user, a few cents). Baseline: case-22 passed 3 of 3 ($0.107); case-21 fails.
  - **Decision rule fixed now:** stop and ask the user if case-23 fails in 1 or more of 3 runs (a flake on a case that was 3 of 3 before is a signal), or if case-22 or case-21 changes result, or if I read the auditor's text and it calls the skipped recommendations required even on a pass. Options then: (a) soften `NOTES_HEADING` and the auditor's use of notes, (b) accept the risk as residual, (c) drop this plan's change.
- E2E (user, about $0.6): `mean` and a trivial goal through to `audited`. Pass: the `mean` notes are imperative and none are cut; the trivial plan is approved; after the implementer and fix rounds `mean(b"12")` is rejected and `mean([1e308, 1e308])` is finite; the trivial task is not failed for ignoring notes. **Branch:** if the notes are imperative but the implementer still ignores them, record that as the result (the advisory policy means the auditor will not catch it) and offer the user the next options; do not tune further in this plan. One run each is a sample of one.
- C3: README, `docs/spike-findings.md` (both rounds, the 559-character miss, caps check, case-23 and the policy, E2E result).

## Residual
- Notes widen the diff; the advisory policy means gaps can still go unfixed (the plan-25 outcome), now with a better chance of being acted on.
- A model can still return `revise` for a gap; that blocks the plan as today, visibly.
- Prompt text cannot be unit-tested for behaviour; tests pin text only.
