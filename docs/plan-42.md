# Plan 42: the work plan 41 deferred

Goal: settle the two items plan 41 left open. Decided; no code changes. Reviewed by `plan-reviewer`; its findings are folded in.

## Decisions
1. **No hint asks for `DEEPENING.md`; it stays unreadable in stages.**
   - Evidence: in one spike read (final grill path) claude's own permission check refused it, not nono (`docs/spike-findings.md`, plan 41, second round). That is one run on one path.
   - Untested candidate fix: add plugin dirs to `--add-dir` in `build_command` (today `--add-dir` carries only the worktree, and only when `config.confine`, `runner.py:26`). Plugin dirs already get a read-only nono `--read` grant when confined (`runner.py:29`), which partly answers the write-access worry. Not verified: which stages set `confine`, and that the change would make the read succeed. It would need its own spike and security check.
   - Nothing shows `DEEPENING.md` improves output, so the change is not worth making now.
   - The `Skill` call's text reached the agent on 3 of 5 paths with the strong hint (none with the original hint). Whether the skill is useful without `DEEPENING.md` is unmeasured.
   - Docs: README:153 already says it could not be read and no README example names it; no other file outside the plans and spike-findings mentions it (grep). One stale line in `spike-findings.md` (the first round's "still unknown") is annotated.
2. **No audit skill is enabled, and the baseline vs. skill eval is not run now.**
   - `codebase-design` is about interfaces and seams and was only tried on plan and review; nothing suggests it helps an audit that judges diffs.
   - Even with a strong hint it was called on only 3 of 5 paths, and the final-grill path already used 7 of 8 turns.
   - Cost: the eval is capped at $12 by default (2 runs, each capped by `max_usd` 6.0, per eval run); the real cost is lower. Corpus size and how the budget accumulates across resumes are unverified.

## Revisit when
- a skill exists that targets what the audit judges, or
- evidence appears that `DEEPENING.md` matters.

## Procedure if the audit experiment is ever run
- Both runs use `eval-audit --stage-config`. Only `stage_plugins.audit` and the audit skill line in `stage_prompts.audit` differ; keep the skill hint out of the baseline.
- Run the baseline first, in a fresh eval directory, with no smoke runs in between (`find_previous` takes the latest complete earlier directory covering the same cases). Never resume across configs: that raises `ConfigChanged`. Restart `serve` between runs.
- Expected `compare` lines: "AUDIT_CONFIG differs" and "audit plugins differ".

## Pass rule (feasible with what is recorded)
- `compare` shows config, plugin and prompt differences, per-case verdict changes and total cost. Records hold outcome, findings and cost only: no turns or subtype, and no per-call cost.
- Pass: no new majority-wrong case and no new `pass` on a fail-labelled case, judged with `audit_report.exit_code` and the report's false-pass and false-fail lines. Single sampled flips at 3 runs per case are noise and do not fail it.
- `?` (infra) outcomes in `compare` are the visible trace of budget or turn trouble (inferred from the code, not run). A turns or per-call budget clause would need a new record field, a code change, so it is out of scope.
