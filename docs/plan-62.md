# Plan 62: a harness abstraction around the agent invocation

## Problem
Claude-specific knowledge is spread over `command.py` (argv, flags, env), `classify.py` (result JSON shape), `RunnerConfig` and `Settings.claude_executable`, and `SANDBOX_PROFILE = "nolabs-ai/claude"`. Supporting another harness means editing all of them. `SubprocessRunner` (launch, timeout, kill, process records, sandbox wrap) and everything downstream (`step`, `call_agent`, stages, ledger, reaper) are already harness-neutral.

## Decided
- A `Harness` protocol in `factory/harness.py`: `build(prompt, config, executable, add_dirs) -> Invocation(argv, env)` and `parse(stdout, exit_code) -> Outcome`, plus `name`, `default_executable`, `sandbox_profile` and capability flags. The runner keeps the executable (it also needs it for the sandbox read grant).
- `ClaudeHarness` is a move of `build_command` and `classify`; behaviour unchanged. `PromptTooLarge`, `MAX_PROMPT_BYTES` and the leading-`-` check stay in neutral `command.py`, called from `build`.
- `build_command` and `classify` stay as permanent one-line wrappers over `ClaudeHarness`. No test is rewritten and no shim is deleted.
- `RunnerConfig` stays as is. A harness maps the fields it supports.
- Capabilities (fail-fast at startup, `SettingsError`, CLI exit 2, no free-text fallback):
  - `supports_json_schema`: plan, review and audit stages.
  - `supports_plugins`: always required by the plan stage (grilling is chosen per task and uses a `.claude-plugin`), and by the `plugins` and `stage_plugins` settings.
  - `supports_budget`: required by every stage (`check_budget`, ledger cost).
  - `supports_tool_rules`: required by any stage with allowed or disallowed tools or `confine`. Without it a harness could silently drop the implementer's shell ban and the read-only stages.
- Executable: `claude_executable` and its default are untouched. A neutral executable key is deferred to the second-harness plan.
- `harness` setting, default `"claude"`, allowed in the global config. Not to be confused with the existing harness-home names (`~/.harness`, `harness_home()`).
- Audit identity: the harness name goes in eval meta, the resume guard, the bundle (`KEPT`), `render_markdown` and `compare` (a "harness differs" note, not a refusal). A missing field reads as `claude`, applied inside `shareable_meta` so it stays idempotent. Bundle `SCHEMA` stays 1 (the field is optional).
- Scope: the abstraction and `ClaudeHarness` only; tests use a fake harness.

## Increments (one commit each; guard = covered by existing tests, no red)
0. Grep the tests for `build_command`, `classify(`, `claude_executable`, `nolabs-ai/claude`, `SANDBOX_PROFILE`, `ANTHROPIC_API_KEY`, `KEPT`, `audit_config`, and the `init` template text. List the hits here before starting. Also confirm nothing but `runner.py` calls `build_command` and nothing but `step.classify_run` calls `classify` (`verify_task`, `run_task`, `recover_task` unchecked so far).
1. Guard (refactor): add `harness.py` with the protocol, `Invocation` and `ClaudeHarness.build`; `build_command` delegates. Guarded by `test_command.py`.
2. Guard (refactor): move the `classify` logic into `ClaudeHarness.parse`; `classify` delegates. Guarded by `test_classify.py`.
3. Red: `SubprocessRunner(..., harness=fake)` launches `fake.build`'s argv with the runner's executable and merges its env. Green: add `harness=` (default `ClaudeHarness()`).
4. Red: a confined run passes `--profile <fake.sandbox_profile>` to nono; a harness with no profile passes none. Green: the runner passes `profile=harness.sandbox_profile` explicitly. Guards: the gate's `profile=None` and the `nolabs-ai/claude` assertions in `test_cli_wiring.py`.
5. Red: a run with a fake harness is classified by `fake.parse` through `call_agent` and `step`. Green: `RunResult` gets a defaulted parser field (Claude), `SubprocessRunner` sets it, `classify_run` uses it. Existing fake runners are untouched.
6. Red: the `harness` setting: unknown name raises `SettingsError` naming the key; default is claude; `claude_executable` still selects the binary; allowed in global config. Green: registry and settings key; construct the harness at both runner sites (`build_context` and `_real_eval_runner` in `cli/base.py`).
7. Red (parametrised, one case per capability): startup refuses a harness lacking a capability a stage needs. Green: the check in `build_context`, covering the CLI, `serve` and eval-audit. Note in the README that `list` and `show` can now fail on this too.
8. Red: a new eval's meta records `harness`; resuming under another harness raises `ConfigChanged`; an old meta without the field resumes as claude. Green: `_meta` and the guard in `audit_eval.py`; the server's `eval_now` passes the name.
9. Red: bundle meta carries `harness`; an old bundle or meta without it comes out as `claude`, idempotently through `_bundle_run` and `load_run`. Green: `KEPT` and `shareable_meta` in `audit_share.py`; `render_markdown` shows it next to the model.
10. Red: `compare` notes "harness differs" when the names differ. Green: `audit_compare.py`.
11. Docs: README and AGENTS.md (the `harness` setting versus harness home; the capability refusal); the `init` template gets a commented `# harness = "claude"` line (check its prefix test).
12. Verification gate: `uv run ruff check`, the full suite in the background with `-n auto`, the `verify` skill, then `audit-commits`.

## Risks (hidden Claude coupling left for the second-harness plan)
- `process.STRIPPED_ENV` holds `ANTHROPIC_API_KEY`, shared with the gate (`gate_sandbox.py`, pinned in `test_runner.py`). Another harness's key would reach the gate; fix by letting the harness contribute secret names and stripping the union.
- Claude tool names and `permission_mode="acceptEdits"` are hard-coded in `stages.py`, `plan_task.py`, `audit_task.py` and `review_plan.py`; the `init` template shows Claude tool names.
- The plugin format is Claude's: `settings._runs_code` checks `.claude-plugin/plugin.json`; the grilling plugin and `GRILL_PREAMBLE` assume Claude skills.
- `MAX_PROMPT_BYTES` and the leading-`-` check assume the prompt travels as one argv string; `audit_task._room_for_diff` imports `MAX_PROMPT_BYTES`.
- `RunnerConfig` stays Claude-flavoured; a neutral rename may be needed later.
- The capability flags are a guess at what a second harness lacks; revisit when one is added.
- Old settings files and audit bundles predate the `harness` field and default to claude.
