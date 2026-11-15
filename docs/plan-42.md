# Plan 42: the work plan 41 deferred

Goal: settle the two items plan 41 left open. Decided; no code changes.

## Decisions
1. **`DEEPENING.md` stays unreadable in stages; no hint asks for it.** Making it readable would mean adding plugin dirs to `--add-dir` in `build_command`, a code change that needs its own security spike (does a plugin dir stay unwritable in stages that allow Edit or Write?). Nothing shows `DEEPENING.md` improves output, so the cost is not justified. `SKILL.md` alone loads (plan 41 spike). README and `docs/spike-findings.md` already say this and no README example names `DEEPENING.md`, so no doc change is needed.
2. **No audit skill is enabled, and the baseline vs. skill eval is not run now.** `codebase-design` is about interfaces and seams; it was only tried on plan and review, and nothing suggests it helps an audit that judges diffs. The eval could cost up to about $12 (two runs at the default `max_usd` of 6.0) for an unproven benefit.

## Revisit when
- a skill exists that targets what the audit judges, or
- evidence appears that `DEEPENING.md` matters.

## Procedure if the audit experiment is ever run
Unchanged from plan 41: both runs use `eval-audit --stage-config`; only `stage_plugins.audit` and the audit skill line in `stage_prompts.audit` differ; restart `serve` between them; keep the skill hint out of the baseline; `compare` will print "AUDIT_CONFIG differs", which is expected. Before running, read `audit_compare.py` and write the pass rule (no case flips from correct to wrong; cost and turns inside the audit budget; no `error_max_turns`) into this file.
