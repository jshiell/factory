# Plan 37: `factory clone <repo>`

Goal: a command that clones a GitHub repository into the harness home as `<home>/<name>/repo`.

## Decisions
- Argument: `owner/repo`, `https://github.com/o/r[.git]` or `git@github.com:o/r[.git]`; other hosts refused.
- Mechanism: `git clone -- <url> <dest>` via subprocess (argv list, `stdin=DEVNULL`); git output inherited. Refuse if the target exists.
- Home: `FACTORY_HOME`, then `home` in the global `<home>/factory.toml`, then `~/.harness`. A project `factory.toml` and `--config` are ignored (no project yet).
- A repo at `<home>/<x>/repo` is keyed as project `<x>` (otherwise every clone would key as `repo`).
- Names that case-fold to `_shared` or `factory.toml` are refused. An existing `<home>/<name>` with no `repo/` is allowed.
- Needs no server and no repo. Exit codes: 0 ok, 1 refused or git failed, 2 usage or settings error.

## Behaviour
- Parse with a full match into (form, owner, name); strip `.git` before validating; build the canonical URL from the parts (`https://github.com/o/r.git`, or `git@github.com:o/r.git` for the SSH form). Host match is case-insensitive.
- Owner and name match `[A-Za-z0-9._-]+`; `.`, `..` and a leading `-` are refused.
- Target exists: `<path> already exists; not overwriting`, exit 1, no git run.
- Git failure: exit 1; `rmdir` `<home>/<name>` only if this command created it (never `rmtree`).

## Files
- New `orchestrator/src/factory/clone_repo.py`: `parse_repository`, `clone_repository(spec, io)`.
- `settings.py`: `_project_dir` keys `<home>/<x>/repo` as `<x>`; public wrapper for the home including the global file's `home`.
- `cli.py`: `clone` parser; route `init` and `clone` through `main(rest, io, None)` inside the existing `SettingsError` branch (`factory init extra` becomes a usage error).
- README: commands table row; the server-exemption sentence (~86); keying rule (~92); home precedence (~94); note a clone has no `target/`.

## Existing tests touched
- `tests/test_cli_wiring.py` init test (~107) is the model; the `:94` migrate exemption needs no edit.
- `tests/test_settings.py` default-path tests must stay green.

## Increments (one test, one impl, one commit each)
Tests redirect GitHub to a local bare repo with `GIT_CONFIG_COUNT` + `url.<file>.insteadOf`, `GIT_TERMINAL_PROMPT=0`.
0. Spike (throwaway): insteadOf redirects https and scp forms; `GIT_CONFIG_COUNT` works; `protocol.file.allow` does not block; a failed clone removes its destination but not the parent. Stop and replan if any fails.
1. Red: `owner/repo` -> canonical https URL, name `repo`.
2. Red: https URL with/without `.git`; SSH form.
3. Red: `..`, `.`, leading `-` refused. Guards: other host, credentials, extra segments, empty name. Red: `_shared` / `factory.toml` case-folded refused.
4. Red: clone via redirect creates `<home>/r/repo` with the commit; prints path; home from `FACTORY_HOME`.
5. Red: global `home` honoured without `FACTORY_HOME`; `FACTORY_HOME` wins.
6. Red: existing target refused with exact message, contents untouched.
7. Red: git failure exits 1, leaves no `<home>/r` (and no `<home>` if this command created it; spike: git leaves the intermediate directories it made, so cleanup `rmdir`s each created one, deepest first). Red: pre-existing `<home>/r/state/x` survives.
8. Red: `load_settings` for a repo at `<home>/r/repo` gives `state_dir == <home>/r/state`; a repo elsewhere named `repo` unchanged.
9. Red: `factory clone` with no argument outside a repo exits 2 with a usage error. Green: routing. Guards: existing target via CLI exits 1; `factory init` unchanged.
10. README; full suite in background with Monitor; `verify`.

## Out of scope
Updating an existing clone, `--dest`, branch/depth options, a default `project_subdir` for a clone.
