# Plan 36: a harness home with one subdirectory per project

Goal: state and worktrees live under a predefined directory (default `~/.harness`), one subdirectory per project, instead of inside each repo. The home is configurable.

## Decided by the user
- Project key: the basename of the repo directory (`~/.harness/<basename>`). Two repos with the same basename collide; documented, not solved.
- Layout: `~/.harness/<project>/state` and `~/.harness/<project>/worktrees`. The repo stays where it is.
- Home precedence: `FACTORY_HOME` env var > `home` in `factory.toml` > `~/.harness`.
- `state_dir` and `worktree_root` stay as per-project overrides and win over the derived paths.
- No migration; the README tells users to move the directory by hand.
- Review defaults accepted ("Fold"): `~` is expanded in `home`, `state_dir`, `worktree_root` and `FACTORY_HOME`; a relative `FACTORY_HOME` is rejected; README line 114 (stale JSON wording) is fixed.

## Facts (read from the code; reviewer-confirmed)
- All path defaults are in `settings.py:load_settings` (lines 67-68). Everything else (`db_path`, `ledger_path`, `gate_cache_dir`, `tasks_dir`, token, lock, procs, evals) derives from `state_dir`. No other `src` module reads `repo/".factory"` or `repo/".worktrees"`.
- `_PATH_KEYS` are joined to the config directory without resolving, so a configured `repo = ".."` has `.name == ".."`.
- `stages.py:71` `factory_paths` is the refusal list of `worktree_clean._safe_to_clean`, not a sandbox list. Moving the dirs out keeps it correct.
- Task records store absolute worktree paths; `clean_tasks._describe` (line 42) marks any task whose worktree is not `worktree_root/<id>` as `outside_root` and skips it.
- Worktrees outside the repo are already covered end to end by `tests/test_cli_wiring.py:67-68` (`worktree_root = tmp_path/"wt"`).
- Tests asserting the old defaults: only `test_defaults_come_from_the_git_toplevel_of_the_cwd` (lines 23, 24, 28, 29 of `tests/test_settings.py`). `test_worktree_clean.py:103`, `test_verify_fix_loop.py:346` and `test_gate_cache_dir_is_under_the_state_dir` build their own paths. Every fixture repo is `tmp/repo`; no conftest sets HOME.
- `build_context` does `state_dir.mkdir(parents=True)`, so `~/.harness` is created on first use.

## Unverified assumptions
- Real nono lets agent runs and the gate write under `~/.harness` (agents get `--allow-cwd --read <worktree>`, gate gets `--allow <state>/gate-cache/<id>`). Every test uses a fake nono. Settled by the spike (S).
- uv, run from `<wt>/target`, behaves the same when the repo is no longer an ancestor. Settled by the spike.
- `Path.home()` raising `RuntimeError` when HOME is unset, and `expanduser` with `HOME=""` returning `/`, are from memory, not run. The C5 tests exercise them.

## Increments (one test, one implementation, one commit each)
0. **Test isolation (infrastructure).** Autouse fixture in `test_settings.py`: `monkeypatch.setenv("HOME", tmp)` and `delenv("FACTORY_HOME")`. No env parameter on `load_settings`. Own commit.
1. **C1 default home (red).** No config: `state_dir == <HOME>/.harness/repo/state`, `worktree_root == .../worktrees`. Approved edit: lines 23, 24, 28, 29 of the old default test, plus renaming it (the defaults now come from home + toplevel basename). Green: derive from home and `repo.resolve().name`, computed only when an override is missing.
2. **C2 key safety (red).** `repo = ".."` and a symlinked repo path give the resolved basename; an empty or `..` key raises `SettingsError` naming `repo`.
3. **C3 toml `home` (red).** `home = "h"` gives `<config dir>/h/<key>/state`. Add `home` to `_KNOWN` and `_STRING`-type checking.
4. **C4 `~` expansion (red).** `home = "~/x"` gives `<HOME>/x/<key>/state`; expand before joining to the config dir. Then a second red for `state_dir = "~/s"` and `worktree_root = "~/w"`.
5. **C5 `FACTORY_HOME` (red, one commit per behaviour):** it beats the toml `home`; an empty value is ignored; a relative value raises `SettingsError` naming `FACTORY_HOME`; `~` is expanded; HOME unset (`RuntimeError`) or `HOME=""` raises `SettingsError` instead of escaping `run()`.
6. **C6 overrides (guard).** Only `state_dir` set: `worktree_root` still derives from home, and the reverse. A bad `FACTORY_HOME` with both overrides set does not error.
7. **S spike (throwaway, not in the suite).** Real nono: one implementer run and one gate run with worktree and gate cache under `~/.harness`; confirm the gate's `uv run` still works. Record in `docs/spike-findings.md`. If denied, stop and replan.
8. **C7 README.** Config block (lines 93, 95): `home`, `FACTORY_HOME`, new defaults, the same-basename collision. Manual move note: move `<repo>/.factory` to `<home>/<project>/state`, and keep `worktree_root = ".worktrees"` in `factory.toml` until old tasks are cleaned (otherwise `clean` skips them as `outside_root`). Fix lines 114 (tasks live in SQLite) and 227 (`procs/` path). Note that worktrees are no longer beside the repo's `.git`.
9. Run the full suite in the background and wait with Monitor, then the `verify` skill.

## Out of scope
Migration or refusal when `<repo>/.factory` exists; same-basename collision handling; moving `factory.toml` out of the repo; the `spike/*.py` scripts (throwaway, hard-code `.worktrees`); creating the project dir as 0700 (the token is already 0600).

## Risks
- Existing users' in-repo state silently stops being used; the README note is the only mitigation. The user's own factory repo has a live `.factory/` and `.worktrees/`, so the manual move applies immediately.
- nono or uv behaviour under `~/.harness` is untested until step 7.
