# Plan 96: remove the task CLI, leaving the web UI as the interface

Drafted 2026-11-28, revised after a plan review. Approved 2026-11-28.
Source: user request. Base: `main`.
**Depends on plan 95** (waivers in the web UI, implemented). Do not start increment 1 until plan 95 is implemented, because until then the CLI is the only place to waive a required reviewer note.

## Decided
- Keep `serve` and `ui` (`serve` hosts the web UI, `ui` prints the login link), plus `eval-audit`, `eval-export`, `init` and `clone`. Drop `migrate`.
- Accept that approve and merge lose their CLI-only TTY barrier; the web UI approves for anyone with a session. The README already says so.
- **Waivers:** add them to the web approve and retry forms first (plan 95), then remove the CLI.
- **Stranded file records:** `serve` refuses a repo with leftover file process records and says "run `factory migrate` first". Change that message to a manual instruction (check no agent is alive, then delete the `procs/` directory), in the commit that removes `migrate`. `serve` does not clear records itself.
- **Removed commands:** an unknown command goes straight to argparse before any repo context is built, with a hint ("removed; use the web UI: `factory ui`").
- **Accepted losses, listed in the README:** per-task costs (`costs <id>`); clean results and per-task problems; branch-deletion detail in the clean preview; rebase's old audited sha; the keep-plan preview extras (failure reason, the "audited and still mergeable" warning); scriptable output (`new` printing the id, `advance` blocking with exit codes), which survives only through the JSON API.
- Leave the CLI-only JSON routes in place; removing them is a later plan.

## Findings (survey and review; the greps are re-run in increment 0)
- The web UI covers `new` (grill checkbox), `list`, `show`, `advance`, `answer`, `approve`, `reject`, `merge`, `rebase`, `retry`, `recover`, `abandon`, `clean` and `costs` for all repos, except for the accepted losses above.
- Web actions run in-process through `WebActions`; the web UI never calls the JSON API.
- `server.py:37` imports `_new_eval_dir` from `factory.cli`, and many tests import `Context`, `IO`, `main`, `run` from it. `factory.cli` is trimmed, not deleted.
- `run()` builds the repo context for any command not in `_NEEDS_NO_REPO` or `_SERVER_COMMANDS` before argparse runs; that is why the unknown-command routing is needed.
- `_ui` and `_with_host` live in `cli/tasks.py` and must survive: move them to `admin.py`.
- Many tests named for the CLI really test server or service behaviour through the `ctx` fixture in `test_cli.py` (a real `repos_app` in a `TestClient`), and about ten tests in `test_cli_wiring.py` use `new`, `advance`, `show` or `list` as the only end-to-end driver, including the only real-subprocess checks of agent confinement.
- Modules probably used only by the removed commands: `question_text`, `migrate`, and in `waiver.py` the terminal prompt helpers. Unverified until increment 0.

## Increments (one commit each, full gate green)
0. **Survey, no commit.** Re-run the greps and record the hits under Deviations:
   - every `cli/` command against its web route;
   - importers of `question_text`, `migrate`, `waiver.approval_prompt`, `parse_answer`, `split_quoting_waived`, `TOOL_CONFIG_HEADING`, `process_registry.clear_file_records`, `Context.probe`, `Context.serve`, `group_alive`, `client.unscoped` and `client.repo`;
   - callers outside `src/` and `tests/` (`spike/`, scripts, `.claude/`, CI) of `factory new|advance|approve|...`;
   - tests that pin "run `factory migrate` first", and template strings that mention `factory `;
   - the smoke count from `uv run pytest -q -m smoke --collect-only | tail -1`.
   - Classify every test that uses a removed command, by name, as delete (presentation only), port to the API (server or service behaviour), or retarget to `ui` or `eval-audit`.
1. **Port the guards** (each commit green on arrival, so each is a guard, not red):
   - `test_cli_wiring.py` advance-driven tests: drive them through the JSON API on the subprocess server (Bearer token from `<home>/_server/token`, advance, poll `/jobs/{id}`). One becomes the replacement smoke test.
   - `test_cli.py` server-behaviour tests (approve digest mismatch and 409, clean with real git, rebase with real git, keep-plan waive and reinstate): rewrite as `TestClient` or API tests.
   - Vehicle tests that only use a command to reach the repo context or the error mapping: retarget to `eval-audit --runs 0` or `ui` (`--repo`, `--config`, bad config, unknown repo, settings warnings in `test_cli_package.py`, no-server, Ctrl-C and 401 in `test_cli_client.py`).
2. **Unknown commands skip the repo context.**
   - Red: running a removed command outside any repo exits 2 with the argparse usage error and the hint, and creates no state; inside a repo it creates no state either. Fold the `watch` test into it.
   - Fix `run()`: any `rest[0]` not in `_COMMANDS` goes to `main(rest, io, None)`.
3. **Remove `new`, `list`, `show`, `advance`, `answer`, `approve`, `reject`, `costs`.**
   - Red: extend the removed-command test to these names.
   - Move `_ui` and `_with_host` to `admin.py`; delete `question_text.py` if nothing else imports it.
   - Fix `test_help_exits_0_and_names_the_commands` to assert the kept names, and trim the cross-increment parametrised tests (`test_no_command_except_approve_calls_approve_plan`, `test_guard_no_command_except_retry_keep_plan_calls_retry_with_plan`, `test_keep_plan_is_only_a_flag_of_retry`).
   - Update the README rows and any message that names these commands in this commit.
4. **Remove `merge`, `rebase`, `retry`, `recover`, `abandon`, `clean`.** Same pattern. Delete `recovery.py`, the `_describe_candidate` re-export and its tests, and the keep-plan CLI tests. Update README and messages in this commit.
5. **Remove `migrate`**: the parser, handler, `migrate.py`, `test_migrate.py`, `test_cli_migrate*.py`, and `"migrate"` in the `factory()` helper of `test_cli_wiring.py`. Change the `claim_state_dir` refusal to the manual instruction and update the tests that pin the old string. Remove `process_registry.clear_file_records` and `FileRecordsBlocked` only if increment 0 shows nothing else reads them.
6. **Dead code**, each item removed only after a grep shows no src reader:
   - in `cli/base.py`: `_ask_with_waiver`, `_run_job`, `_task_path`, `_load`, `_ref`, `_require_repo`, `task_client`, `_coded` and its `failure_codes` import;
   - in `main()`: the `TaskNotFound`/`InvalidTaskId` and `NoRepo` handlers, and the `refused.error == "unknown task"` branch;
   - in `waiver.py`: `approval_prompt`, `parse_answer`, `Waive`, `Reinstate`, `Refuse` (with their tests);
   - `Context` fields and the TTY plumbing that nothing reads, and the `escape_control` re-export if its test goes.
7. **Docs:** the README sweep and the accepted-losses list.
   - Rewrite "Using it" as `serve`, `ui`, then the browser.
   - Rewrite the "Every CLI command except ... is a client" paragraph for `ui` and `eval-audit` only.
   - Fix the TTY notes, the "waive in the terminal" lines (plan 95 added the web forms), "The CLI keeps connecting", the `factory show` and `factory answer` mentions, and the `factory list`, `migrate` and `costs` line in the Tasks section.
   - Update the smoke count only if it changes the documented "about 25". Add the Deviations section, and tick `todo.md` if it lists this (never commit it).
8. Run the `verify` skill, then the `commit-auditor` against this plan.

## Risks and unknowns
- **Hard break.** Every script or habit using the removed commands stops at once. The web UI is still unchecked in a real browser (README: plans 69 to 75).
- **The TTY barrier is gone for good.** Whoever can run `factory ui` (needs the token file) can sign in and approve.
- **Old JSON state.** `migrate` goes; nothing shows anyone needs it, but this is unconfirmed. The old files are never deleted, so an older release can still migrate them.
- **Test blast radius.** About 13 CLI test files plus others import `factory.cli`; increment 1 exists so coverage is ported before anything is deleted.
- **Not verified:** the importer questions listed in increment 0, whether the templates or `docs/` describe the CLI, and the README outside lines 30 to 180.
- No dependency changes expected.

## Deviations and survey (increment 0, 2026-11-28)
- 21 commands, all registered in `cli/__init__.py` `_parser` (:85-120), dispatch `_COMMANDS` (:318-324). Handlers: `tasks.py` (new, list, show, advance, answer, approve, reject, costs, ui), `recovery.py` (retry, recover, abandon, rebase, merge, clean), `admin.py` (migrate, serve, clone), `__init__.py` (eval-audit, eval-export, init).
- Src importers only inside removed handlers: `question_text`, `migrate` module, `approval_prompt`, `TOOL_CONFIG_HEADING`, `clear_file_records`, `FileRecordsBlocked`, `_ask_with_waiver`, `_run_job`, `_task_path`, `_ref`, `_require_repo`, `_coded`, `Context.probe`. `split_quoting_waived` is also used by `retry_task.py` (stays). `Waive`/`Reinstate`/`Refuse`/`parse_answer` (test_waiver.py), `task_client` (test_task_ref.py) and `client.unscoped` (test_client.py:83) are test-only. `_load` and `client.repo` have no call sites. `Context.serve` is used by the kept `serve`. `escape_control` re-export is imported by `tests/test_cli.py:11`.
- Outside src and tests: CI names no command; no `.claude/` or Makefile. Removed commands are named in README (:48-52, :62-77, :146, :154, :162, :183, :199), `spike/` (orphan_e2e, merge_e2e, keep_plan_e2e, rebase_e2e, README) and about 30 historical `docs/plan-*.md` (left as history).
- Tests pinning old strings: test_repo_settings.py:61, test_server_startup.py:125 (migrate); test_rebase_task.py:389; test_advance.py:91; test_cli_wiring.py:192; test_cli.py (239, 256, 557, 565, 697, 827, 996, 1441, 1472). test_web_approve_page.py:46 and test_web_approve_waive.py:34 assert "factory approve" is absent and stay.
- Smoke count: 26 (the README says about 25; unchanged).
- 145 test functions invoke removed commands: test_cli.py 111, test_cli_wiring.py 14, test_cli_migrate_procs.py 9, test_cli_migrate.py 4, test_cli_client.py 3, test_cli_repo_option.py 3, test_cli_package.py 1. Per-test classification (delete / port to API / retarget to `ui` or `eval-audit`) is made per increment, from the survey report.

## Deviations (increments 1-7)
- Increment 1 was done as API-driven wiring tests plus guard tests (commits 8a84183 and 633aa59), not as per-test ports; vehicle tests were retargeted to `ui` and `eval-audit` (eec0bee).
- `settings.py`'s in-repo-state message also carried a `factory migrate` instruction; it was reworded in increment 5 (ece67ec) with the `claim_state_dir` message.
- `process_registry.clear_file_records`, `FileRecordsBlocked`, `FileRecordsCleared` and `still_running`, and `Context.probe` and `group_alive`, were removed with `migrate` (ece67ec).
- The unknown-command hint reads "task commands were removed; use the web UI: `factory ui`" and is printed on any argparse "invalid choice" error (1f63885), not only for the old command names.
- Kept, left as a follow-up: `Context.repo` (`test_cli_client.py` passes `repo=`) and `IO.stdin` (passed positionally in about ten test files).
- Vacuous tests deleted in increment 3 (be1938a): `test_no_command_except_approve_calls_approve_plan`, `test_keep_plan_is_only_a_flag_of_retry` and `test_guard_approve_prints_no_warning_at_the_item_limit`.
- `test_task_ref.py` was deleted with `task_client` (faf1486).
- Increment 7: the README smoke count was wrong ("25 tests"); the count is 26 and was changed. Seven scripts in `spike/` drive removed commands, not the four the survey named: also `abandon_clean_e2e.py`, `branch_gc_e2e.py` and `housekeeping_e2e.py`. Each got a one-line banner and a note in the `spike/README.md` table; none was rewritten.
