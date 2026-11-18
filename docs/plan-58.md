# Plan 58: widen the ruff rules

## Problem
`pyproject.toml` selects `E, F, I` (E501 ignored). Measured with `ruff check --select B,UP,SIM,C4,RUF --ignore RUF059`: 56 hits, 15 auto-fixable. `PT`, `PTH` and `RUF059` are out of scope (hundreds of edits, style only; `os.path.abspath` is used deliberately for non-resolving paths, plan 57).

The measurement replaced the config select, so `RUF100` was unreliable. Reviewer reading of the 4 hits: `cli.py:759` and `tests/test_verify_task.py:86` (E731 lambda) and `tests/test_cli_migrate_procs.py:3` (F401 on the shadowed `ctx` fixture) are false positives; `cli.py:45` is real (`escape_control` is used in `cli.py` itself). Unverified until the merged-config re-run.

## Scope
Config plus the flagged lines. No behaviour change intended; no new public API. Existing tests are the safety net. A new test only if a fix touches an untested path (RUF015 empty case).

## First
1. Re-run with the final merged config (`select = E,F,I,B,UP,SIM,C4,RUF`, `ignore = E501, RUF059`) and record every hit by file:line, including the unnamed RUF005 #2, RUF015, RUF043 ×5, C408 ×9, the RUF001 count (`test_waiver.py:52` may be 2). This replaces the hit list if it differs.
2. `grep -rnE 'phase|Phase' orchestrator/src orchestrator/tests` for f-string, `%`, `str()`, `format()` uses of `Phase` (UP042: `(str, Enum)` to `StrEnum` changes `str()`). `watch.py` itself was read and does not depend on it. If anything else does, `# noqa: UP042` with the reason. If clean, no new test.
3. `grep -rn __cause__ orchestrator`: if nothing reads it, B904 uses `from None`.
4. Confirm UP042 and RUF043 are stable (non-preview) in the locked ruff.
5. Run the verification suite in the background (about 73s measured, AGENTS.md says about 100s; tool timeout 120s).

## Increments (one commit each; gate green before each; fixes in the same commit as the rule)
1. `Enable B lint rules`: `task.py:104` `raise ... from None`. `test_verify_fix_loop.py:256` B017: keep `Exception` with `# noqa: B017` and the reason (the test means "any later git failure" after `.git` is deleted). The three `zip(x, x[1:])` sites (`test_command.py:116`, `test_gate_sandbox.py:21,63`) become `itertools.pairwise(x)`, which clears B905 and RUF007 here.
2. `Enable UP lint rules`: `ruff check --select UP --fix` (UP017 ×3, UP037 ×2; confirm only UP fixes are in the diff and `timezone` is no longer imported). UP031 (`answer_questions.py:20`) by hand to an f-string. UP042 per First 2.
3. `Enable C4 lint rules`: C408 ×9 dict() to literals (tests).
4. `Enable SIM lint rules`: SIM105 ×6 to `contextlib.suppress`, keeping comments (`worktree_clean.py:16`). SIM117 ×10 via `ruff check --select SIM117 --fix --unsafe-fixes`, then review the diff: order is preserved (outer context first); in `tests/test_lock.py:47-49` the lock stays outside `pytest.raises`.
5. `Enable RUF lint rules` (RUF059 ignored): RUF005 ×2 by unpacking. RUF009 `cli.py:96`: `field(default_factory=ps_probe)` (stateless closure, so same behaviour; import `field`). RUF015: check the empty case first; if an `IndexError` is caught or a generator is involved, write the test first, else `next(iter(...))`. RUF043 ×5: per site, escape or `re.escape` a metacharacter meant literally, `r""` only for an intended regex (converting `\\x` to `\x`). RUF001: write the characters as escapes (`"\xa0"`, and so on), data unchanged, no suppression. RUF100: remove only `cli.py:45`'s noqa, only if the re-run reports exactly it; hand-edit the "(re-exported)" note; keep the E731 and `ctx` F401 noqas.

Commit messages: `Enable <group> lint rules`.

## Verification
`cd orchestrator && uv run ruff check`, then the full suite in the background with Monitor. Plan 53's CI runs the same gate. Mutation checks do not apply (no new logic) except any RUF015 test.

## Risks
- UP042 changing enum string behaviour (First 2).
- RUF043 regex-meaning change; RUF015 exception-type change.
- SIM117 reordering contexts (reviewed in the diff).
- `ruff --fix` touching rules other than the one being enabled (scope with `--select`).

## Decisions (adopted)
Option B scope; B017 keeps `Exception` with noqa; SIM117 via scoped unsafe fix plus diff review; five commits, C4 and SIM split.

## Files
`orchestrator/pyproject.toml`, the src and tests files listed above, `docs/plan-58.md` (this).
