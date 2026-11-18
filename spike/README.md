# spike/

Throwaway probes and end-to-end scripts from building the factory. They are **not production code**, are not run by the
test suite, and several were written against an older shape of the product. Findings live in
[`docs/spike-findings.md`](../docs/spike-findings.md) and the plan documents; this directory is the evidence.

## Before you run anything

- Run from the repo root, in **your own terminal**, not inside an agent's nono session (the nono and gate-sandbox probes
  say so in their docstrings).
- Some scripts spend real money (see Cost). Each caps its own runs; the caps are soft.
- `nono_impl_probe.py` writes a marker line into `~/.claude.json` (it restores a byte-for-byte backup at the end, even on
  error) and plants fixtures and canaries under `$HOME`. `nono_probe.py` and `nono_probe2.py` plant canaries under `$HOME`,
  `$TMPDIR` and `~/.claude/nono-probe-<pid>` and remove them unless given `--keep`.
- `probe.py`, `probe2.py`, `probe3.py` and `run_one.py` create git worktrees and branches in **this** repo
  (`.worktrees/`). Remove them afterwards with `git worktree remove` and `git branch -D`.
- The "e2e" scripts that exercise `clean`, `merge` and friends build a synthetic repo; they never touch this one.

Run a script with `uv run --project orchestrator python spike/<script>.py [args]` (the ones that import `factory` need
that; `probe.py` and `run_one.py` need only `python`).

## Scripts

Plan numbers refer to `docs/plan-<n>.md`. "TTY" means it prompts y/N and needs a real terminal.

### $0 (no API calls)

| Script | Plan | Needs | What it checks |
|---|---|---|---|
| `abandon_clean_e2e.py` | 11 | TTY, network, `uv` | `abandon` and `clean` over tasks in every state, including a gate cache filled by a real `uv` run |
| `branch_gc_e2e.py` | 14 | TTY, `uv` | `clean` and failed-task branches (empty, with a commit, dirty worktree, unset base) |
| `clean_gate_e2e.py` | 13 | real `nono`, `uv`, network | Three gate runs through `verify_task` with the real gate sandbox |
| `housekeeping_e2e.py` | 8 | TTY, `uv` | `recover`, `retry` and `clean` on a synthetic repo |
| `keep_plan_e2e.py` | 19 | TTY, `uv` | `retry --keep-plan`, with and without a TTY |
| `merge_e2e.py` | 7 | TTY, `uv` | `merge` and its y/N prompt |
| `rebase_e2e.py` | 15 | `uv`, your real env and global git config | `factory rebase` never asks for a signature |
| `test_freeze_e2e.py` | 12 | real git, real gate | Real git and the real gate (isolated git config, temp dir) |
| `orphan_e2e.py` | 21 | real `nono`, `ps`, `uv` | SIGKILL the driver mid-run, then `recover` / `abandon` leave no processes. Starts real processes (`sleep 31337`, `sleep 31338`) |
| `gate_sandbox_probe.py` | 55 | real `nono`, `uv`, network | The gate sandbox: network, filesystem and older `uv` versions. Wipes and rewrites `gate-sandbox-probe-out/` on every run. Flags: `--smoke` (only `uv --version` through the sandbox prefix), `--tmp DIR` |
| `cli_probe/` | 5 | a TTY to be meaningful | A tiny uv package that reports the cwd, `isatty`, and whether `/dev/tty` opens. Entry point `cli-probe` |
| `nono_impl_probe.py` | 9 | real `nono`, `uv`; `$0` unless `--paid` | The nono sandbox for the implementer (its docstring is the authority). `--paid` adds two real `claude` runs (about $0.3 at most) |

### Paid (real `claude` calls)

| Script | Plan | Cost (from its docstring) | What it checks |
|---|---|---|---|
| `probe.py` | unlabelled | not stated | The first three open questions. Argument: `q1`, `q2` or `q3` |
| `probe2.py` | 2 | not stated | Isolation and tools, repeated `--allowedTools`, `--` before the prompt, the error subtype. Argument: `iso`, `repeat`, `dashdash` or `badmodel` |
| `probe3.py` | 2 | not stated | `--append-system-prompt`, `--max-turns`, process groups, a bad API key. Argument: `appendprompt`, `maxturns`, `noprompts`, `pgroup` or `apikey` |
| `run_one.py` | unlabelled | `--max-budget-usd 0.50` | One headless agent in a fresh worktree; dumps the raw result. Optional argument: the task prompt |
| `e2e.py` | 2 | budget 0.5 | One real task end to end |
| `e2e3.py` | 3 | budget 0.5 | The same with the audit stage |
| `e2e4.py` | 4 | about $0.30 each for plan, review and audit; $0.50 to implement | Goal, plan, review, **human approval (asks on stdin, never auto-approves)**, implement, verify, audit. Needs a TTY |
| `audit_probe.py` | 3 | about 4 runs, $0.30 cap each | How the auditor judges good, skipped, monkeypatched and written-test changes |
| `plan_probe.py` | 4 | about 6 runs, $0.30 cap each | The planner and plan reviewer |
| `nono_probe.py` | 6 | about 7 runs, $0.3 to $0.7 total | What `nono` blocks for `claude`. Flag: `--keep` |
| `nono_probe2.py` | 6 | about 7 runs, about $0.3 | Round two of the same. Flag: `--keep` |
| `review_probe.py` | 25 to 27 | $0.30 cap per run, 3 runs per cell | Whether other review prompts find more gaps. Arguments: variants (`current`, `probe1` to `probe6`) and plans (`mean`, `is_even`, `join_words`, `parse_ints`, `messy`), comma-separated |
| `review_variance.py` | 32 | about $0.03 a run | How much the required-note set varies run to run. Flags: `--runs N` (8), `--plans a,b`, `--max-usd X` (3.00), `--no-confine`, `--dry-run` (print the command and stop) |

## Output files

Ignored by git: `spike/*.stdout.json`, `spike/*.stderr.txt`, `e2e-state/`, `e2e3-state/`, `e2e4-state/`,
`nono-probe-out/`, `nono-impl-out/`, `review-probe-out/`, `review-variance-out/`, `gate-sandbox-probe-out/` and
`audit-eval-out/state/`.

Tracked as a record of what the probes found:

- `p2-*.exit.txt` from `probe2.py`, `p3-*` from `probe3.py`, `p4-*` from `audit_probe.py`, `p5-*` from `plan_probe.py`.
  Each holds the command's exit code. Their `.stdout.json` and `.stderr.txt` partners are ignored, so they are not here.
- `audit-eval-out/`: the plan 22 measurement of the auditor (20 cases, 3 runs each), written by scripts that were
  removed when plan 23 promoted them into `factory.audit_eval`.

## Known stale references

Some older documents name scripts that no longer exist under those names: `spike/audit_cases.py` and
`spike/audit_eval.py` (plan 22; now `orchestrator/src/factory/audit_eval.py`) and `spike/cli_probe.py` (plan 5; it is the
`cli_probe/` package). The documents are historical and were left as they were.

## Not checked

Home-directory use by `gate_sandbox_probe.py` and `orphan_e2e.py`, and the cost of `probe.py`, `probe2.py` and `probe3.py`.
