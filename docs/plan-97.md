# Plan 97: reorder the README so use and configuration come first

Drafted 2026-11-28, revised after a plan review. Approved 2026-11-28.
Source: user request. Base: `main`. Follows plan 96 (the README is now web-only).

## Goal
A new reader can install, start the server, log in and configure a repo from the first screens. Design, platform measurements, known limits and development come last. Documentation only: no code, no behaviour change. Current-behaviour facts are kept; only history and stale claims go.

## Decided
- Target README order:
  1. Intro (add a one-line 'who it is for')
  2. Quick start: short requirements list (including `gh`, needed by `clone`, and GitHub only), `init`, `clone`, `serve`, `ui`, first task; links to Configuration and Platform verification
  3. Using the web UI: task flow only (create, answer, approve, advance, merge, rebase, retry, recover, abandon, clean, costs, jobs, grilling)
  4. Configuration: file locations and precedence, the TOML block, plugins, stage prompts, `harness` and `sandbox`, the old-layout startup refusal and the `target/` settings warning
  5. Commands reference: the table, exit codes, `clone`, `eval-audit`, `eval-export`, and the CLI-client facts now under Server (`ui` and `eval-audit` need a running server, `--repo`, `--config`, Ctrl-C keeps the job running)
  6. Remote access and security: the Bearer token, the `Host` and `Origin` guard, bind rules, `server_allowed_hosts`, the HTTP-only limits, session and cookie caveats, signals and interrupted jobs. **Owned here**; `docs/api.md` links back to it
  7. Design
  8. Platform verification (the whole table, moved from the top; Requirements links to it)
  9. Known limits (gains the 'older builds cannot read new task rows' line)
  10. Development, with the repo layout table (Layout moves once, here)
- The HTTP API (endpoint table, jobs, errors, the 202 job behaviour, and the JSON locations of data the CLI used to show) moves to a new `docs/api.md`. The README keeps a two-line pointer in Remote access and security.
- Delete as history: the `factory watch` note, the root `factory.toml` move instruction repeated elsewhere, and 'What the CLI no longer does' (after its current facts are moved), and the rest of 'Upgrading from a single-repo layout' once its startup refusal and `target/` warning are kept.
- Keep, one line each: 'older builds cannot read new task rows' (Known limits) and the `target/` settings warning (Configuration).
- The JSON locations from 'What the CLI no longer does' go to `docs/api.md`: `GET /costs?task_id=`, `result.outcomes` in `GET /jobs/{id}`, `commits_ahead`, `branch_exists` and `branch_empty` in `GET /clean-preview`, `old_audited_sha` in the rebase job result.
- No pointer to the plan 96 loss list stays in the README (user decision). That reverses plan 96's 'listed in the README'; `docs/plan-96.md` still holds the list.
- The 'Not yet checked in a real browser' bullet is dropped (user decision; README:174 contradicted README:159).
- The Web UI list has three destinations: Using the web UI, Remote access and security, and Known limits or Design (transaction semantics, row id mismatch, vendored htmx, style rules, the long job-error bullet). A mixed bullet is split at sentence boundaries, each sentence moved verbatim.

## Findings (README, 455 lines; verified in the plan review)
- Order today: intro, Layout, Requirements, Platform verification (9 rows), Using it, Commands, What the CLI no longer does, Cloning, Evaluating the auditor, Server (with the API table), Web UI, Grilling, Configuration, Design, Development, Known limits.
- Configuration is nested under 'Using it' and Web UI under Server, so moving them changes heading levels. Quick start and Remote access do not exist yet. The Server section (109-141) has no single destination; 141 is mostly CLI usage.
- The Web UI list is about 22 bullets plus 7 sub-bullets, and holds three kinds of content, several bullets mix them.
- References that a `](#` grep misses: seven `#platform-verification` links; quoted 'see "X"' refs (Cloning a repo, Web UI, Evaluating the auditor, Failure diagnosis, Configuration, Server, including one inside the TOML block); positional ones ('below', 'see the last bullet', 'the costs view', 'the sandbox setting'); and one already dangling ('see the gate section', no such section).
- AGENTS.md says 'see the README' for the sandbox grants, so that paragraph must stay findable.
- Stale figures: '~2000 tests' (README 15 and 424), '26 tests, ~3s' and '~25s in parallel' (README 425), and 'about 25 tests' in AGENTS.md.

## Increments (one commit each; docs only, so no TDD cycle; the full suite runs once at the end)
0. **Survey, no commit.** Record under Deviations:
   - the full reference list above, re-derived from the file;
   - a grep for `README` across `AGENTS.md`, `orchestrator/src`, `orchestrator/tests`, `.github`, `spike` and `docs` (other than `plan-*.md`), including whether the `factory init` template mirrors the TOML block;
   - a destination for every top-level Web UI bullet and every Server paragraph;
   - each sentence of the sections to delete, sorted into history or current behaviour.
1. **1a: move whole blocks.** Reorder existing blocks into the target order. Allowed edits: heading levels and new empty headings for Quick start and Remote access and security. Check with `git diff --color-moved=zebra --color-moved-ws=allow-indentation-change`.
2. **1b: split Server.** Move each Server paragraph to its destination from the survey; cut the API table, jobs and errors verbatim into `docs/api.md`; add the README pointer and the back-links to Remote access and Configuration.
3. **1c: cross-references.** Fix every reference from the survey, including positional ones, the one in the TOML block and the dangling one.
4. **Quick start.** Fold Requirements and 'Using it' into it; add `gh` and the GitHub-only note; link to Platform verification and Configuration.
5. **Split the Web UI list** into its three destinations, as decided. Fix 'see the last bullet'.
6. **Tidy Configuration.** Lead with file locations and precedence; group the notes by topic; add the startup refusal and the `target/` warning lines. No setting changes.
7. **Move the JSON locations** into `docs/api.md`, then delete 'What the CLI no longer does'.
8. **Delete the remaining history** as decided, drop the 'Not yet checked in a real browser' bullet, add the 'older builds' line to Known limits, and fix the stale figures (README and AGENTS.md). Use wording that cannot go stale where a count is not needed.
9. **Final check.** Re-run the reference check and the README grep, read the README once top to bottom, run the full suite, then run the commit-auditor against this plan.

## Risks
- Facts lost in a move: 1a is a pure move checked with `--color-moved`; later splits move text verbatim.
- Broken references: the survey list is the checklist, re-run in 1c and 9.
- `git diff --color-moved` availability: confirm in increment 0; fall back to comparing sorted lines.
- Platform verification becomes harder to find: linked from Quick start and Requirements.
- 'Older builds cannot read new task rows' was not re-verified against the store code; the line is carried over as written and flagged as unverified in the README wording check at increment 0.

## Deviations
(none yet)
