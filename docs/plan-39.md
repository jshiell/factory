# Plan 39: `factory clone` through `gh`

Goal: `factory clone` runs `gh repo clone` instead of `git clone`, so it uses the user's `gh` login (private repos, `git_protocol`, credential helper) the same way `factory watch` does. Decisions agreed; revised after review (plan-reviewer, 2026-11-13); awaiting approval to implement.

## Spike findings (gh 2.104.0, 2026-11-13, public `octocat/Hello-World`)
- A full `https://` or `git@github.com:` URL clones and keeps that protocol in `origin`.
- A bare `o/r` takes its protocol from the host setting: `gh config get -h github.com git_protocol` is `ssh` here (the unqualified `gh config get git_protocol` printed `https`, which is not the one gh used).
- `github.com/o/r` is accepted and also follows `git_protocol`. A bare `o/r` with `GH_HOST` set goes to that host, so `github.com/o/r` pins the host.
- `HTTPS://GitHub.com/o/r` (upper-case scheme and host) fails in gh; `parse_repository` already lowercases the prefix.
- Logged out (empty `GH_CONFIG_DIR`, no token): gh refuses with exit 4 and "run `gh auth login`", for a bare slug and a URL alike. So no pre-check is needed.
- `--no-upstream` is accepted on a non-fork. Missing parent directories (`a/b/c/repo`) are created. `-- --depth 1` after `--no-upstream` is accepted.
- Not verified: fork behaviour with `--no-upstream` (no fork cloned); whether gh itself inserts `--` before calling git.

## Decisions (agreed)
1. **Protocol: gh decides for a bare slug.** A bare `o/r` is passed as `github.com/o/r` (host pinned, protocol from gh's `git_protocol`). A spec typed with a prefix is passed as `parse_repository`'s canonical URL, keeping the typed protocol. Consequence: a bare slug's origin may change from https to ssh for existing users.
2. **Forks: `--no-upstream`.** One `origin` remote only, as `watch`'s `origin_slug` assumes.
3. **gh missing or not logged in:** clean exit 1, no fallback to `git clone`. Missing: our message. Logged out: gh's own stderr ("run `gh auth login`"), which stays inherited.
4. **Success line prints the origin actually cloned** (`git remote get-url origin` read back), so the user sees the protocol they got. If the read fails, print the target passed to gh.
5. **No `GIT_TERMINAL_PROMPT=0`:** production never set it; behaviour unchanged.

## Design
- `gh_target(spec) -> str` in `clone_repo.py`: bare spec gives `github.com/<owner>/<name>` from `github_slug` (so `.git` is stripped, case kept); prefixed spec gives `parse_repository`'s URL. `parse_repository` and its tests stay unchanged.
- `clone_repository(spec, io, run=_run)`: injected runner, like `origin_slug(repo_dir, run=None)` in `github.py`. `cli.py` is untouched: `clone` runs with no `Context` (`_NEEDS_NO_REPO`), so no wiring is possible or needed. `_run` is a local wrapper around `subprocess.run`, so `clone_repo.py` still matches the `subprocess.run(` scan in `test_subprocess_sites.py` and only its entry's wording changes.
- argv: `["gh", "repo", "clone", target, str(destination), "--no-upstream"]`, `stdin=DEVNULL`, stderr inherited (live progress and gh's own errors). No `--` (with gh it means "rest goes to git").
- Option injection is stopped by validation, which stays first: owner and name cannot start with `-`; targets start with `github.com/`, `https://` or `git@`; the destination is absolute.
- `FileNotFoundError` is caught inside `clone_repository` (`_clone` in `cli.py` only catches `RepositoryError`), as `_watch_locked` does for `watch`.
- Directories: factory creates none itself (`_missing_ancestors` only records; the clone creates parents, verified for gh). On failure it still removes what the clone created.
- The success-line read-back runs `git remote get-url origin` in the user's own fresh clone (same precedent as `origin_slug`), so the `clone_repo.py` exclusion in `test_git_hardening.py:41` stays.

## User-visible strings touched (grepped; tests asserting them)
- `already exists; not overwriting`: `test_clone_repo.py:140` (exact), `test_cli_wiring.py:151` (substring). Unchanged text.
- `not a GitHub repository`: `test_cli_wiring.py:137`. Unchanged. (`test_cli_watch.py:130-137` is `origin_slug`'s separate message.)
- `cloned <url> into <destination>`: no test asserts the wording (`test_clone_repo.py:106` checks only that the destination path appears in stdout). The text changes to show the origin read back.
- New: "gh is not installed" message.

## Existing tests changed (named)
Moved to the fake runner, asserting the argv destination instead of a cloned `hello.txt`: `test_clone_repo.py` lines 101-129 (clone under harness home, global config home, `FACTORY_HOME` wins) and 144-159 (failed clone cleanup, keeps existing project dir). The real-bare-repo `github` fixture (lines 71-87) is dropped with them. Lines 132-141 (existing target refused) keep their assertions and gain the fake as the "never called" check. `test_clone_repo.py:162-167` (case) and the `parse_repository`/`github_slug` tests are untouched.

## Increments (one test, one impl, one commit each; fake runner throughout)
1. Red: `gh_target("o/r") == "github.com/o/r"`.
2. Red: `gh_target("o/r.git") == "github.com/o/r"`; guard: owner/name case kept (`Owner/Repo`).
3. Red: a prefixed spec gives the canonical URL, `HTTPS://GitHub.com/o/R` gives `https://github.com/o/R.git`.
4. Red: `clone_repository(spec, io, run=fake)` calls the argv above with `stdin=DEVNULL`. Same commit: the `_run` wrapper, the `test_subprocess_sites.py` entry reworded to `gh repo clone`, and the tests at lines 101-129 moved to the fake.
5. Red: the success line prints the origin read back. Guard: if the read fails, it prints the target.
6. Red: the runner raising `FileNotFoundError` exits 1 with "gh is not installed" and nothing on stdout.
7. Guard (behaviour exists): a non-zero exit returns 1 and removes the directories created; the fake creates `harness/r/repo` and its parents, then returns 1. Rewrites lines 144-148.
8. Guard: a failed clone keeps an existing `harness/r/state/x`. Rewrites lines 151-159.
9. Guard: the runner is never called for an existing destination, `-owner/r`, a reserved name, or `gitlab.com`.
10. README: the `clone` row (needs `gh` logged in; bare slug follows `gh`'s protocol setting; forks get no `upstream`).
11. Full suite in the background with Monitor; `verify`.

## Out of scope
Cloning non-GitHub hosts, extra git flags (`--depth`), changing the `<harness_home>/<name>/repo` layout, a `gh auth status` pre-check.
