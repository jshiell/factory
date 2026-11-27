# Plan 92: multiple repositories in one server

Drafted 2026-11-26, revised after two plan reviews (2026-11-26). Approved 2026-11-26; implemented (increments 1-36); see Deviations.
Source: `todo.md` open items "support working on multiple projects" and "server_allowed_hosts should be global".
Base: `main`.

## Problem
One `factory serve` manages one repository: `settings.repo`, `worktree_root`, `main_branch`, the stages and the job table are fixed at startup from that repo's `.factory/factory.toml`, which also holds the server's own settings (port, host, allowed hosts). To work on two repositories you run two servers on two ports with two tokens.

## Decisions (settled with the user, 2026-11-26)
1. Data stays per repo: one SQLite db per repo in its own state dir. No shared db, no data migration.
2. Task addressing is repo-qualified: `<repo>/<task id>`. Ids stay unique only within a repo. The CLI defaults the repo from the current directory and accepts `--repo`.
3. One global job worker. Accepted consequence: a long job in one repo delays jobs in the others.
4. `cap_usd` stays per repo (each repo's own ledger).
5. Repos are discovered by scanning `<home>/*/repo` at server start. No reload: `factory clone` prints "restart the server". A server with zero repos starts and serves an empty list.
6. The old layout is refused, not migrated. A repo not at `<home>/<name>/repo` is refused with a message to move it there and run `git worktree repair` from the new location.
7. Repo config stays in each repo's `.factory/factory.toml`.
8. `repo`, `state_dir`, `worktree_root` and `home` are refused in a repo config (the layout is fixed). `--config` stays, as a file location only.
9. A repo whose lock is held, that has leftover process records, or whose reaping is uncertain is skipped and reported; the other repos start.
10. Repo names match `[A-Za-z0-9._-]+`, not starting with `.` or `-`. Reserved names (`_shared`, `_server`, `factory.toml`) match case-insensitively. Symlinked repo paths (`<home>/<name>`, `<home>/<name>/repo`) are refused; `<home>` itself may be a symlink. `require_safe_id` is unchanged: task ids stay `[A-Za-z0-9_-]+` (dots would change branch, worktree and gate-cache names).
11. An old `<state_dir>/token` is ignored; serve prints a one-line warning that it is unused.
12. `eval-audit` stays per repo (`<repo state>/evals`).
13. In-repo state is no longer migrated by `factory migrate`. The refusal for in-repo state tells the user to move the files to `<home>/<name>/state` and run `factory migrate`, which keeps working on the fixed state dir.
14. CLI commands that are pure clients (all but `watch`, `migrate`, and `eval-audit`'s local read) load global settings only, check the repo name and use a repo-prefixed `ServerClient`. `watch`, `migrate` and `eval-audit` load full repo settings.
15. `factory list` with no repo resolvable from cwd and no `--repo` is an error naming `--repo`.

## Target shape
- `<home>/<name>/repo`, `<home>/<name>/worktrees`, `<home>/<name>/state` per repo (unchanged for repos made by `factory clone`). `<home>` comes from the same rule as `harness_home()` and is carried as a new `Settings.home` / global-settings field, not re-read from the environment at use sites.
- `<home>/_server/` holds the server lock and the token (login codes and sessions stay in memory).
- `server_port`, `server_host`, `server_allowed_hosts` are read from the global config only, and are an error in a repo config naming the file. Other keys keep today's layering.
- A named `GlobalSettings` type (home, port, host, allowed hosts) is returned by `load_global_settings()`. A no-repo `Context` for `serve`/`ui` carries it and the registry instead of a store, ledger and stages.
- `api_routes(ctx, jobs, runner)` is extracted from `_build_app` and mounted once per started repo under `/repos/{name}`; the single-repo adapter mounts the same factory at the root until the old routes are removed. `GET /repos` lists started repos and skipped repos with their reason. A JSON catch-all `/repos/{repo}/{rest:path}` answers an unknown repo (so `ServerClient`, which calls `.json()` on every response, never sees plain text).
- UI: `/ui/` lists tasks across repos; pages under `/ui/repos/{repo}/...`; login codes stay server-level. One `TaskChanges` is subscribed to every repo's store and `JobStore`.
- `ServerClient` takes a `prefix`; `/ui-login-codes` and `/repos` are unscoped calls.
- `JobRunner.submit` takes the `JobStore` per job; the single worker runs each job against its repo's context.
- `watch` stays one process per repo (lock name `watch`, `watch.json` in the repo state dir). It does not exclude `serve`; it talks to it.
- Repo from cwd: the cwd (resolved) under `<home>/<name>/repo` at any depth, or `<home>/<name>`, or a task worktree under `<home>/<name>/worktrees/<id>`. Anything else needs `--repo`. Compare resolved paths.
- Security: the token moves to `<home>/_server/token`, and `<home>/_server` is added to `private_dirs` (agent runner and gate sandbox) in the same commit. `private_dirs` only filters granted directories that contain a private dir; step 0 verifies what the nono profile grants. One token now controls every repo (larger blast radius than today).

## Consequences and risks
- `build_stages`, `WebActions`, server clean and merge-preview and `cli.build_context` read `settings.repo`, `worktree_root`, `main_branch`; each becomes per-repo-context.
- The old-layout refusal must not go in `load_settings`: `_project_dir` supports a repo anywhere today. New loaders (`load_global_settings`, `load_repo_settings`) carry the new rules; `load_settings` is untouched until the CLI and fixtures move, then reduced. Scale: 5 test files call `load_settings` directly, 14 call `_build_app`/`create_secured_app`; most tests inject a `Context`. Indirect reliance is not counted.
- `test_cli_wiring.py:71` runs end to end with `--config` setting `state_dir`, `worktree_root` and `server_port`; it moves to the `<home>/<name>/repo` layout in increment 24.
- Holding each started repo's lock means `factory migrate` for a started repo needs the server stopped; a skipped repo's lock is free.
- A skipped repo keeps stale `running` jobs until a later start (same as today).
- Cross-repo index and `/ui/jobs` must survive one repo's unreadable rows; UI job links carry the repo; `_action_refusal` and the per-task stream use that repo's job store. Redirects in `web/routes.py` (~246, 363, 415, 504, 568, 624, 671) and about 44 template hrefs hard-code `/ui/tasks/...`.
- Printed hints with bare ids (`cli/tasks.py` ~91, 92, 128, 190; `cli/recovery.py` ~78, 95, 103; watch's gave-up comment) print `<repo>/<id>`.
- A checkout outside `<home>` now always needs `--repo`; README says so.
- `factory-sample/factory.toml` uses `state_dir`/`worktree_root` and is a root `factory.toml` (already refused today); it moves to the new layout.

## Tests that pin affected output (to be re-grepped exactly at step 0)
- Serve line `serving on http://127.0.0.1:{port}; token in {state_dir}/token`: `test_cli_serve.py:37`, `test_cli_serve_host.py:38,54`, `test_cli_serve_allowed_hosts.py:47`.
- `no factory server is running`: `test_cli_ui.py:45`. Token path: `test_cli_serve.py:36,54,95`, `test_cli_client.py:66`, `test_serve_stopping.py:60`, `test_server_security.py:42,48`, `test_settings.py:171`.
- Global-config refusal of `server_*` (increment 2 flips these; regex tests, no "not allowed in the global config" string is pinned): `test_settings_server_host.py:40-43`, `test_settings_allowed_hosts.py:42-45`.
- Per-repo `server_*` (increment 3 edits): `test_settings.py:279`, `test_cli_wiring.py:71`, `test_settings_server_host.py:35,48,56`, `test_settings_allowed_hosts.py:37,51,65`, `test_settings_ipv4_hosts.py:27`. Per-repo `home` (increment 4 edits): `test_settings.py:119,315,324,332,352,494,500,600`; `test_clone_repo.py:140,151` write `home` to the global config and are unaffected.
- Others to review as their increments land: `test_server*.py`, `test_web_*.py`, `test_cli_watch.py`, `test_watch.py`, `test_clean_tasks.py`, `test_cli_migrate*.py`, `test_reap_orphans.py`, `test_stages.py`, `test_gate.py`, `test_init_config.py`; fixtures `webfixtures.py`, `serving.py`, `webmerge.py`, `slowrecords.py`.
Rule: an existing assertion is edited only when it pins old wording, an old path or an old setting location; each edit is listed under Deviations with its reason.

## Increments (TDD: one test, one implementation, one commit each; suite green at every commit; G = guard, not red)
0. Base gate on `main`; exact greps (lists above); record results. Read-only spike: what the nono profile grants and whether an agent could read `<home>/_server`, another repo's state or worktrees.
1. G: `JobRunner.submit` takes the `JobStore` per job.
2. Global config accepts `server_host` and `server_allowed_hosts` (edit the two tests above).
3. Repo config refuses `server_*`, naming the file (edits listed above).
4. Repo config refuses `home` (edits listed above).
5. `Settings.home` and `load_global_settings()` returning `GlobalSettings`.
6. Token in `<home>/_server`, client read, 401 message, serve line, and `<home>/_server` in `private_dirs` (runner and gate), in one commit.
7. Serve warns about an unused old `<state_dir>/token`.
8. Clone: `_server` reserved, case-insensitive.
9. Clone refuses a leading `.`.
10. Clone prints "restart the server".
11. `discover_repos(home)`: names, case-insensitive reserved names, symlinks (not `<home>` itself); returns problems.
12. `load_repo_settings(home, name)`: refuses `repo`, `state_dir`, `worktree_root`; keeps the root `factory.toml`, config-location and `project_subdir` checks; in-repo state refused with the "move it to `<home>/<name>/state`" message.
13. `RepoRegistry`: started repos plus skipped repos with reasons; unknown name raises.
14. `claim_server(registry)` through an `ExitStack`: `_server` lock, each repo's lock, reap, job interruption; skip-and-report; the `migrate` lock message names the server.
15. G: extract `api_routes(ctx, jobs, runner)` from `_build_app`.
16. Mount it per repo under `/repos/{name}`; the adapter mounts it at the root.
17. JSON 404 for an unknown repo; `GET /repos` lists started and skipped repos.
18. `ServerClient` prefix; `/ui-login-codes` and `/repos` unscoped.
19. Repo name from cwd (pure function, resolved paths).
20. `--repo` option (name validated against the charset).
21. `repo/id` helper returning the prefixed client and the bare id.
22. Printed hints use `<repo>/<id>`.
23. `factory list` with no resolvable repo errors naming `--repo`.
24. CLI client commands built from global settings plus repo name; `test_cli_wiring` moves to the new layout; `watch`, `migrate`, `eval-audit` use `load_repo_settings`.
25. UI index lists tasks across repos (survives an unreadable repo).
26. UI task page and task events under `/ui/repos/{repo}`, with that repo's stores.
27. UI action pages and redirects (and template hrefs).
28. UI new-task repo selector.
29. UI jobs page.
30. UI clean page.
31. UI costs page. SSE subscribes every store and job store (covered by 25-26; guard where already passing).
32. `serve` and `ui` take the no-repo path: registry, `claim_server`, new serve line, the registry test seam on `Context`.
33. Watch: repo-scoped client; gave-up hint.
34. Remove the old unscoped routes and the adapter; edit the tests that pinned them.
35. Reduce `load_settings` to what `eval-export` and `init` need.
36. README, init template (`init_config.py` still lists per-repo paths only), `factory-sample`, `todo.md` (remove the two items); `verify` skill.

## Step 0 results (2026-11-26)
- Base gate on `main` (95e2361): ruff clean; 3649 passed in 43.9s.
- Greps: as listed above. Also: `state_dir`/`worktree_root` config text in `test_settings.py:110,169,180,341,391,401,412` and `test_cli_wiring.py:70,71`. `load_settings(` is called on 122 lines across 5 files. `create_secured_app(` appears 17 times in 14 files; `_build_app(` is not called from tests. `"/tasks/` appears on 92 lines (`test_server.py` 44, `test_server_merge.py` 20, `test_watch.py` 18, `test_server_unreadable.py` 6, `test_client.py` 1 and others). Fixtures building `Settings(`/`Context(`: `webfixtures.py` (15), `webmerge.py` (21).
- Sandbox spike (from the README and `sandbox.py`, not run live): an agent reads only what is granted. `<home>/_server` is unreadable by default; the exposure is a granted directory containing it (executable dir, uv dir), which `without_private` filters. Adding `<home>/_server` to `private_dirs` in increment 6 is defensive and correct. README line ~365 needs rewording in increment 36. Plugin dirs under `<home>/_shared/plugins/*` do not contain `_server`.

## Deviations
- Increment 1: `JobRunner.submit` takes `jobs` as an optional argument (the constructor store is the default), so the existing callers and tests stay unchanged. It becomes required when the registry replaces the constructor store (increment 32).
- Increment 3: the `project` fixture in `test_cli_wiring.py` writes `server_port` to `<FACTORY_HOME>/factory.toml`; the two tests that expect no global config (`test_init_writes_the_global_template...`, `test_clone_refuses_a_non_github_repository...`) remove that file first. `test_settings_ipv4_hosts.py` `load_with` writes the global config. `test_serve_stopping.py` was fixed through the fixture.
- Increment 4: tests pinning a per-repo `home` were removed or moved to the global config: deleted `test_a_relative_home_in_dot_factory_config_resolves_against_the_repo_root` (superseded by the refusal test), `test_a_home_in_the_config_is_relative_to_the_config_directory` (the global-config relative home is covered by the existing global tests) and `test_factory_home_beats_the_home_in_the_config` (duplicate of the global-config `FACTORY_HOME` test); moved the tilde and plugin-dir tests to the global config.

- Increments 6–14: the restart notice from `factory clone` goes to stderr; `private_dirs` is a `Settings` property; `load_or_create_token` takes a directory; the leading-dot refusal covers the owner part of a clone spec as well as the name; the `migrate` lock message names the server; the in-repo state message ends "and run `factory migrate`".
- Increment 15: `api_routes` returns an `Api` (routes, actions, clean preview and enqueue) rather than a bare route list, because the app state needs the last three; the `/ui-login-codes` route moved out of it into the app builder (it is server-level).
- Increment 16: `JobRunner()` takes its default job store as optional; each repo gets a `_BoundRunner` that submits to that repo's `JobStore`. The multi-repo app is `_build_repos_app(registry)`, separate from `_build_app` (the adapter) until increment 32.
- Increment 17: the catch-all also answers a known repo's unknown path with a JSON 404; a known repo's own unmatched paths inside its mount still get Starlette's default 404 (as the root routes do today).
- Increment 18: `ServerClient.unscoped()` returns a prefix-free view; `for_repo(name)` (increment 21) returns a scoped one.
- Increments 22–23: `Context` gains `repo` (qualifies printed ids) and `repo_required` (set only by the real CLI path in increment 24, so the single-repo adapter tests keep working until increment 34). A malformed task ref raises `InvalidTaskId`; `NoRepo` exits 2.
- Increment 24: `serve` and `ui` still load the single repo config (`_STILL_SINGLE_REPO`) until increment 32. The legacy app also mounts its routes under `/repos/<project dir name>` as a bridge so the scoped client works against it; the bridge goes in 32/34. `--help` anywhere on the line skips building the context, so it works outside a repo. `--config` moves only the repo's own config file (`load_repo_settings(..., config=)`). `test_cli_wiring` keeps its legacy server by giving `serve` a `--config` that holds the state and worktree paths; the settings-warnings test in `test_cli_package` now runs `list` from a repo under the home.
- Increment 20: `--repo` is a pre-parser option that may appear anywhere on the line.
- Increment 25: the index first had no SSE; it landed in 31.
- Increment 26: an unknown repo on a UI route returns the JSON 404; template hrefs take `base`. The "is X, not reviewed" message is left bare.
- Increment 27: a `_task_routes(prefix)` helper serves the per-task pages under `/ui` and `/ui/repos/{repo}`.
- Increment 28: the repo selector is at `/ui/tasks/new`, with a `?repo=` preselect; the grill default comes from the selected repo.
- Increments 29 and 31: costs and jobs pages are per-repo sections, not merged lists; no cross-repo cost total.
- Increment 30: clean pages are per repo at `/ui/repos/{repo}/clean`, linked from the index.
- Increment 32: the `ServerContext` seam; `--config` and `--repo` are ignored for `serve` and `ui`; a repo whose lock is held, or whose orphans cannot be proved dead, is skipped rather than refusing to start; reaped lines are repo-prefixed; skipped-repo stderr lines are new; login and static routes were added to the repos app.
- Increment 33: `Options.repo` and `ServerClient.repo`. Only the gave-up comment prints `<repo>/<id>`; the other watch comments still print bare ids (`merging.py` 26, 41, 192; `planning.py` 87; `retrying.py` 45).
- Increment 34: tests migrated to one repo named `proj` via a `repos_app` helper; `_require_repo` is unconditional (`repo_required` removed); `test_web_sentence_case` uses `zdata-proj`; the `test_cli_wiring` hack was removed.
- Increment 35: `load_settings` was deleted outright (`init` and `eval-export` did not need it); about 20 old-layout tests were deleted (cwd and git-toplevel discovery, `state_dir`/`worktree_root`/`repo`/`home` keys, `_project_dir` keying); an explicit `--config` that does not exist is now an error (c8366a8); the `FACTORY_HOME` rules are guarded on the global loader (db4c141).
- Increment 36: `factory-sample` does not exist in the repo, so there was nothing to move. `todo.md` is untracked; its two items were removed in the working copy only.
- Decision 14 (pure client commands load global settings only) was not implemented: every repo command goes through `load_repo_settings` and `build_context`, so a repo command reads the repo's config and creates its state dir. The unknown-repo consequence (`factory --repo <typo> list` creating `<home>/<typo>/state`) was confirmed and fixed after the audit, see below.
- Increment 32: `test_the_app_it_serves_requires_the_token` was deleted; its check lives in `test_serve_serves_every_repo...` (`test_cli_serve.py`).
- Increment 35: also deleted `test_an_explicit_config_inside_the_configured_repo_is_refused_from_any_cwd` (a cwd variant; the refusal stays pinned by `test_an_explicit_config_inside_the_repo_but_outside_dot_factory_is_refused`), `test_a_relative_path_in_the_global_config_resolves_against_its_own_directory` and `test_plugin_dirs_follow_a_configured_home`.
- Increment 36: the README sandbox text now names `<home>/_server` beside the state dir.
- After the audit: `_repo_context` refuses a repo with no `<home>/<name>/repo` checkout (`settings error`, exit 2) before any state is created; test `test_an_unknown_repo_is_refused_without_creating_its_state` in `test_cli_wiring.py` (red first).
- After the audit: `test_cli_migrate_procs.py` now also asserts the `migrate` lock message names the running `factory serve` (a guard; the wording came with increment 14).

## Not verified
- What the nono profile grants (step 0 spike); README line ~360 records a live check where other state and worktrees were denied.
- Starlette's default 404 body type (handled by the JSON catch-all regardless).
- What `clean_worktree` does with `factory_paths` (`stages.py:99`); `<home>/_server` is not added there unless step 0 shows a need.
