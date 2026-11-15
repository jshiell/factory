# Plan 41: skills per stage

Goal: decide which skills each stage (plan, review, implement, audit) can use, and make them measurable. Decisions agreed; revised after review (plan-reviewer); approved to implement.

## Decisions (agreed)
1. **Delivery:** prompt hints via `stage_prompts` for the glossary/ADR and test-quality guidance. `codebase-design` is the only plugin, user-supplied in `<home>/_shared/plugins` (no new code or API).
2. **Implement hint is for the implementer only.** The fixer shares the implement config (`stages.py:87`) and must not be told "write a failing test first".
3. **Eval:** `eval-audit --stage-config` applies the full audit stage config (plugins, stage prompt, global prompt). The default is unchanged.
4. **Audit scope:** build the eval support; enable no audit skill until a baseline vs. skill `--compare` shows no regression.
5. **Turn limits:** if `codebase-design` does not fit `max_turns=8` on a path, drop it there and return to the user with the numbers. Limits do not change.

## Findings the plan rests on
- Stage agents run with `--setting-sources ""` (`command.py`), so user skills are invisible; skills arrive only as plugins via `--plugin-dir`. Plugins with hooks or MCP are refused (`settings._runs_code`).
- Plan, review and audit deny Write, Edit, NotebookEdit, Bash, Glob; the implementer denies Bash, `Bash(git *)`, Glob, NotebookEdit. All run headless. `Agent` is allowed in every stage.
- The eval passes the raw `AUDIT_CONFIG` (`audit_eval.py:74`, meta at `:119`), so it ignores plugins and stage prompts.
- The implementer prompt already says to write the failing test first and that it cannot run commands (`approve_plan.py:40-42`).
- A grilling round used 7 of 8 turns before being raised to 12; plain plan and review keep 8 turns and $0.30.
- Not recommended as stage skills: `verify` (the verify stage runs the gate; no Bash), `diagnosing-bugs` (needs Bash), `code-review` (spawns sub-agents, too big for the audit budget), `research`, `writing-for-agents`, `implement-plan`, `wizard`, `pr`, `triage` (interactive or meta, no matching stage).

## Increments
0. **Spike (throwaway, flagged as such).** Under real nono, on a non-toy repo, for the plain plan path (8 turns), the grilling path (12) and review (8): the skill loads as `codebase-design:<skill>`, triggers when the prompt names it, no denials, turns and cost fit, supporting files readable. Record in `docs/spike-findings.md`. Stop and return if a stage does not fit.
1. **Prompt hints (config and docs, no code).** README example `stage_prompts`: plan and review get "if `GLOSSARY.md` or `docs/adr` exist, use their terms and respect the ADRs" and (only where the spike passes) a line naming `codebase-design`; implement gets two sentences of test-quality guidance (no red/green, no seam confirmation).
2. **Fixer split (TDD), only if needed.** Red: the fixer does not receive the implementer-only hint. `test_stages.py:330` pins the current sharing; changing it needs user approval first.
3. **Eval uses the audit stage config (TDD), one behaviour per commit.**
   - 3a. Red: `run_one`/`run_eval` take a config and the meta records it. Guard: default meta unchanged.
   - 3b. Red: resume with a different config raises `ConfigChanged`; the server job fails with a message (new `test_server_eval.py` test, config-recording runner fake).
   - 3c. Red: meta records a content hash per plugin dir; `compare` reports "audit plugins differ" (new `test_audit_compare.py` test).
   - 3d. Red: `POST /eval-audit` accepts `stage_config: true`, 422 on a bad type, builds `stage_config(AUDIT_CONFIG, settings, "audit")`.
   - 3e. Red: CLI `--stage-config` sends it.
4. **Docs.** README:119 says eval-audit needs `--stage-config` to see plugins and prompts; fix README:117 wording. Template examples keep the `my-plugin` placeholder so `test_init_config.py:30-37` stays green.
5. **Verify.** Full suite in the background (~100s), then the `verify` skill. One commit per green increment.

## Deferred
Enabling any audit skill (baseline eval, eval with skill and `--stage-config`, `--compare`; enable only with no regression).

## Tests to list before changing behaviour
`test_stages.py:229`, `:279-330`; `test_audit_compare.py:81-82`; `test_audit_task.py:22-29` (unaffected while `AUDIT_CONFIG` is not edited); `test_server_eval.py`; `test_init_config.py:30-37`.

## Unverified
Whether any hint or skill improves plan, review or implement output (only audit has a measurement planned); turn headroom on the plain plan and review paths; `allowed-tools` in a skill cannot widen the deny list (spike-findings marks this weak).
