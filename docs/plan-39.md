# Plan 39: `factory clone` through `gh`

Goal: `factory clone` runs `gh repo clone` instead of `git clone`, so it uses the user's `gh` login (private repos, `git_protocol`, credential helper) the same way `factory watch` does. Decisions agreed; awaiting approval to implement.

## Spike findings (gh 2.104.0, 2026-11-13, public `octocat/Hello-World`)
- `gh repo clone <canonical https url> <dir>` clones; origin stays `https://github.com/...`.
- `gh repo clone git@github.com:o/r.git <dir>` clones; origin stays the ssh form.
- Bare `o/r` gets an **ssh** origin here, although `gh config get git_protocol` printed `https` (likely a per-host setting; cause not verified). So a bare slug follows gh's config, not factory's current https default.
- `HTTPS://GitHub.com/o/r` (upper-case scheme and host) **fails** in gh ("error connecting"). `parse_repository` already lowercases the prefix, so passing its canonical URL avoids this.
- Not verified: fork handling (`upstream` remote), `gh` not logged in, `gh` missing, whether `url.insteadOf` redirection applies to gh's inner `git`.

## Decisions (agreed)
1. **Protocol: gh decides for a bare slug.** A bare `o/r` is passed to gh as `o/r`, so gh's `git_protocol` picks ssh or https. A spec typed with a prefix (`https://github.com/...` or `git@github.com:...`) is passed as the canonical URL (prefix lowercased, `.git` appended), keeping the typed protocol. Consequence: a bare slug's origin form may change for existing users.
2. **Forks: `--no-upstream`.** One `origin` remote only, as `watch`'s `origin_slug` and the clone layout assume.
3. **gh missing or not logged in:** clean exit 1 with a message, created directories removed. No fallback to `git clone`.

## Premises
- Validation stays first and unchanged: `github_slug`, reserved names, existing-destination refusal.
- The cleanup of directories the command created on failure stays.
- `FileNotFoundError` from `subprocess.run` is currently uncaught for `git`; with `gh` it must be caught (as `GitHub` does for `watch`).
- Tests currently use real bare repos redirected with `url.insteadOf`. Whether that reaches gh's inner git is unverified; if not, the clone tests move to an injected `run` fake (the `GitHub` pattern) and assert the argv.

## User-visible strings touched (tests that assert them)
- `cloned <url> into <destination>`: `tests/test_clone_repo.py` (clone puts repo under harness home, home resolution tests).
- `<dest> already exists; not overwriting`: `test_clone_repo.py`, `test_cli_wiring.py:117-153`.
- `not a GitHub repository`: `test_cli_wiring.py`.
- New: the `gh` missing / failure messages.

## Files
- `clone_repo.py`: a `gh_target(spec)` returns the bare slug or the canonical URL as above (`parse_repository` keeps its current output); the success line prints the target passed to gh. Run `["gh", "repo", "clone", url, str(destination), "--", ...]` with `stdin=DEVNULL`; catch `FileNotFoundError`; keep cleanup. Take an injected `run` like `GitHub`.
- `cli.py`: pass the runner through `Context` (as `watch` does).
- `tests/test_subprocess_sites.py`: reword the `clone_repo.py` entry to `gh repo clone`.
- `tests/test_git_hardening.py`: no change if the bare `["git"` is gone from `clone_repo.py` (the exclusion can then be dropped).
- README: the `clone` row (needs `gh` logged in).

## Increments (one test, one impl, one commit each)
1. Spike first (not committed): does `url.insteadOf` reach gh's git; fork behaviour; logged-out behaviour. Decides how the tests are built.
2. Red: `clone_repository` runs `gh repo clone <target> <destination> --no-upstream` (fake runner, argv asserted). Red: a bare slug passes `o/r`, a prefixed spec passes the canonical URL. Existing clone tests that need a real clone are moved or kept per the spike.
3. Red: `gh` missing exits 1 with a message and removes created directories.
4. Red: a non-zero `gh` exit returns 1, shows gh's stderr, leaves nothing it created. Guard: a pre-existing project dir keeps its contents (existing test).
5. Guard: validation and existing-destination refusal happen before gh runs (the fake runner is never called).
6. Update `test_subprocess_sites.py`; drop the `clone_repo.py` hardening exclusion if unused.
7. README. Full suite in the background; `verify`.

## Out of scope
Cloning non-GitHub hosts, `--depth`/extra git flags, changing the `<harness_home>/<name>/repo` layout.
