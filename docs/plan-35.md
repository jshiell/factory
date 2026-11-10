# Plan 35: run as a local HTTP server, state in SQLite

Goal: `factory serve` starts a local server; every command the CLI offers today is an API endpoint. State moves from per-task JSON files, `ledger.jsonl` and `procs/*.json` into one SQLite file. No UI.

## Decided by the user (not up for review)
- Starlette + uvicorn (this overrides the old "stdlib only" rule for runtime code; dev rules unchanged).
- `advance` is an async job: 202 plus a job id, client polls.
- Confirmations are preview-then-confirm: a GET returns what the prompt showed plus a digest or sha, the POST must echo it, else 409.
- The CLI stays, as a thin client of the API (Phase 5).
- `merge`, `rebase`, `abandon`, `recover` and `clean` queue behind a running job (FIFO, same worker) instead of failing busy.
- Plus the standing rules: uv, pytest; TDD, one red-green-commit per behaviour; never edit a passing test except via an approved edit list; never push; signed commits.

## Recommendation (user asked me to consider): SQLite, not DuckDB
Task state is small, row-at-a-time, transactional, with a few writer threads. That is SQLite's job (stdlib `sqlite3`, no new dependency, WAL mode). DuckDB is columnar/analytical with a single-writer process model; it only wins for ledger analytics, which `costs` already does in a few lines. Revisit only if cost reporting grows.

## Facts (read from the code, not run)
- `cli.py` (637 lines) mixes parsing, `isatty` checks, `y/N` prompts, locking and printing around domain functions (`approve_plan`, `merge_task`, `retry_with_plan`, `rebase_task`, `clean_tasks`, `advance`). The domain functions already take `store`, `ledger`, digests and shas as arguments and know nothing about the terminal.
- The confirm-what-you-saw guard already exists as arguments: `approve_plan(..., plan_digest, shown_findings=)`, `retry_with_plan(..., shown_digest, shown_findings=, shown_audit_gaps=)`, `merge_task(..., shown_sha=)`. Only `clean` has no equivalent (it re-scans after the prompt).
- Stages use only `TaskStore.save/create/load/list`, `Ledger.append/entries/total/would_exceed` and the `procs/` record functions. `create_with_fresh_id` relies on `create` raising `TaskExists` atomically (today `os.link`).
- `state_lock` is a `flock` on `state_dir/lock`, taken per command, then `reap_orphans` runs. `eval-audit` takes its own lock on its own directory.
- `RUNNING` with no driver means a SIGKILLed driver; `recover` is the human step. SIGTERM/HUP raise `SystemExit` so the runner kills the agent tree.
- Gate caches (`gate-cache/<id>`), worktrees and eval directories are directories, not records; they stay on disk.

## Unverified assumptions (C0 spike checks each before anything is built on it)
- `sqlite3` in WAL mode with one connection per thread gives atomic insert-or-conflict and no `database is locked` under one writer thread plus request threads.
- Starlette `TestClient` can drive sync handlers and a background worker thread deterministically.
- uvicorn's own signal handling lets us run shutdown code that kills the agent tree and marks the running job interrupted.

**C0 result (docs/spike-findings.md):** assumptions 1 and 2 hold. Assumption 3 holds for SIGTERM and SIGINT but not SIGHUP: `serve` installs its own SIGHUP handler. Starlette 1.10 has no `on_startup`/`on_shutdown`; use `lifespan`.

## Design
1. **Storage.** `factory.db` in `state_dir`. Tables: `tasks(id PK, state, data JSON, updated_at)` (the existing dict serialisation, so `Task` and its loader are unchanged; `state` is a column only for listing and filtering), `ledger(seq, task_id, cost_usd, assumed, stage, at)`, `processes(pid PK, ...Record fields)`, `jobs(id PK, kind, task_id, status, result JSON, started_at, finished_at)`. `SqliteTaskStore` and `SqliteLedger` keep today's method signatures so `stages.py` and every domain module are untouched. `create` is `INSERT` and maps `IntegrityError` to `TaskExists`.
2. **Migration.** `factory migrate` imports `tasks/*.json` and `ledger.jsonl` into the DB, refusing if the DB already has tasks; old files are left alone (never deleted by us). Unreadable task files are reported, as `list` reports `skipped` today.
3. **One server per state dir.** The server holds the existing `flock` for its lifetime (second server exits). It runs `reap_orphans` once at startup and marks any job left `running` as `interrupted`. A task left `RUNNING` stays so; `recover` works as today.
4. **Job executor.** One worker thread, FIFO queue, one job at a time (the old one-driver rule). Kinds: `advance`, `eval-audit`, `merge`, `rebase`, `abandon`, `recover`, `clean`. `eval-audit` keeps its own directory and process records so it can't reap the main run's agents. Endpoints that the CLI ran under the lock (`merge`, `rebase`, `abandon`, `recover`, `clean`) validate the request, then enqueue and return 202 with a job id; the digest, sha or token check runs again when the job starts, so a task that changed while queued gives a `failed` job with the 409-style reason and changes nothing. `new`, `approve`, `reject` and reads never queue (as today, they take no lock).
5. **Service layer.** A `service` module holds the orchestration now inside `cli.py` handlers (load, state checks, preview building, calling the domain function, mapping exceptions to typed errors). Handlers return data, never print. Starlette routes are thin over it.
6. **Endpoints** (JSON; errors are `{error, detail}`; 404 unknown task, 409 wrong state or stale digest at request time, 422 bad input; refusals found while a queued job runs land in the job result):

   | Method and path | Replaces |
   |---|---|
   | `POST /tasks` `{goal}` -> 201 | `new` |
   | `GET /tasks`, `GET /tasks/{id}` | `list`, `show` |
   | `POST /tasks/{id}/advance` -> 202 `{job}` | `advance` |
   | `GET /jobs`, `GET /jobs/{id}` | (new) polling |
   | `GET /tasks/{id}/approval` -> goal, plan, risks, notes, `plan_digest`, required notes | the approve prompt |
   | `POST /tasks/{id}/approve` `{plan_digest, waive:[note]}`; `POST /tasks/{id}/reject` | `approve`, `reject` |
   | `GET /tasks/{id}/merge-preview`; `POST /tasks/{id}/merge` `{audited_sha}` -> 202 `{job}` | `merge` |
   | `POST /tasks/{id}/retry` `{}` fresh; with `{keep_plan:true, plan_digest, waive, reinstate, audit_gaps}` after `GET /tasks/{id}/retry-preview` | `retry` |
   | `POST /tasks/{id}/recover`, `/abandon`, `/rebase` -> 202 `{job}` | same names |
   | `GET /clean-preview` -> candidates plus a `token`; `POST /clean` `{token}` -> 202 `{job}` | `clean` |
   | `GET /costs?task_id=` | `costs` |
   | `POST /eval-audit` `{runs, only, max_usd, resume, compare, check_corpus}` -> 202 | `eval-audit` |

   Waive and reinstate are lists of the note texts (as the domain functions take them), not indexes, so a client can't point at a different note than the one it saw; the existing `InvalidWaiver` checks stay.
7. **Clean token.** The only new guard logic: `GET /clean-preview` hashes the candidate list it shows; `POST /clean` recomputes and refuses with 409 if it differs.
8. **Local security.** Bind `127.0.0.1` only. This server can spend money and merge code, so any local process or web page must not be able to call it: a random bearer token is written to `state_dir/token` (mode 0600) at startup and required on every request; requests with a non-loopback `Host` or any `Origin` header are refused (DNS rebinding, browser CSRF).
9. **Serving.** `factory serve [--config] [--port]` (default 127.0.0.1, fixed default port, `--port 0` for tests). SIGTERM stops the worker, kills the agent tree and marks the job interrupted.
10. **CLI.** Untouched and working through the storage phase (same DB). After endpoint parity it is rewritten as a thin client: same commands and output text, calling the API with the token from `state_dir/token`; the `y/N` prompts stay in the client and drive the preview and confirm calls; long commands poll the job. It needs a running server (clear error if none).

## Cycles (red, green, commit each; C0 is a flagged throwaway)
- C0 (throwaway spike, discarded): the three unverified assumptions above; findings appended to `docs/spike-findings.md`. This plan committed first.
- **Phase 1: storage (CLI keeps working)**
  - C1 (red): `SqliteTaskStore` round-trips a task with every field set, including tuples and enum state. Further reds, one each: `create` twice raises `TaskExists`; `load` unknown raises `TaskNotFound`; unsafe ids rejected as today; `list` skips an unreadable row and reports it; `save` replaces. Same cases as `test_store.py`, written as a new file (the existing tests stay as they are while the file store lives).
  - C2 (red): `SqliteLedger` append/entries/total/would_exceed incl. `assumed` and `stage`, same cases as `test_ledger*`.
  - C3 (red): process records in a table: write, load, invalid record reported, `reap_orphans` works unchanged against it (inject the store; the probe/kill/group_alive seams already exist).
  - C4 (red): `migrate` imports tasks and ledger; refuses a non-empty DB; reports unreadable files; leaves old files in place. Guard: imported tasks equal what `TaskStore.load` returned.
  - C5 (red): `build_context` uses SQLite; existing `test_cli.py` passes unedited (if a test builds the file store directly it keeps doing so; any needed edit goes to you as an edit list first).
- **Phase 2: service layer**
  - C6 (refactor, green): move read-only handlers' logic (`list`, `show`, `costs`) into `service` returning data; CLI handlers print it; `test_cli.py` byte-for-byte unchanged.
  - C7 (done): `new`, `reject`, `approve` moved into `service` (approve's five `test_cli.py` patches and the approval tripwire retargeted to `service.py`, approved by the user).
  - C7+ dropped (user decision): `retry`, `recover`, `abandon`, `rebase`, `merge`, `clean` already wrap domain functions with typed errors, so the CLI call sites and their `test_cli.py` patches stay. Phase 3 adds `service` functions only where the server needs data the CLI only prints (merge, retry and clean previews, the echoed digest/sha/token); endpoints call the domain functions through them.
- **Phase 3: server, jobs**
  - C8 (red): `GET/POST` for `new`, `list`, `show` through `TestClient`, 404 on unknown id, error shape.
  - C9 (red): job table and executor: `advance` returns 202, job goes `queued` -> `running` -> `succeeded` with the `AdvanceResult` steps; a second job queues FIFO; `GET /jobs/{id}` unknown is 404. Stages are stubs; a job that raises an unexpected error is `failed` with its message (the worker survives).
  - C10 (red): `merge`/`rebase`/`abandon`/`recover`/`clean` while an `advance` job runs return 202 and run after it, in order; `approve`, `reject`, `new`, reads still work; a queued job whose task changed meanwhile ends `failed` and changes nothing.
  - C11 (red each): approval preview and confirm: stale `plan_digest` is 409 and approves nothing; waive of a non-required note is 422; success queues the task. Same for merge (`audited_sha`), retry keep-plan (digest, waive, reinstate, gaps) and `clean` (token).
  - C12 (red): `retry` fresh maps `RetryRefused` to 409; `recover`, `abandon`, `rebase` map `RecoverRefused`, `AbandonRefused`, `RebaseRefused`, `InvalidTransition`, `CalledProcessError` to a `failed` job with the reason.
  - C13 (red): `eval-audit` as a job: usage errors are 422, own directory, own process records.
- **Phase 4: hardening**
  - C14 (red): missing or wrong token 401; non-loopback `Host` 403; any `Origin` 403; token file mode 0600.
  - C15 (red): startup: second server on the same state dir refuses; orphans are reaped; a `running` job becomes `interrupted`; `ReapUncertain` stops startup with the message.
  - C16: `factory serve` entry point, `--port 0`, SIGTERM path (spike C0 decides how this is tested; if it can't be unit-tested it is labelled and covered by a real run).
  - C17: README (API table, state layout, security note), `docs/spike-findings.md`; `AGENTS.md` test note if the suite time changes. Full suite in the background after each phase.
- **Phase 5: thin-client CLI.** Rewrite handlers over an HTTP client one command at a time, keeping output text. Before starting, grep `test_cli.py` for the exact strings each command prints and list those tests; tests that build `Context` directly are replaced via an edit list you approve first. Add: no-server error, token missing, job polling and Ctrl-C (client stops polling; the job continues).

## Risks
- Queued destructive jobs (`clean`, `abandon`) run later than the user confirmed; the re-check at job start covers task changes, but a long queue means a long gap.
- Phase 2 is the biggest diff. It is split per command and each step is behaviour-preserving, so it can pause after any commit.
- Long-running work on a server thread: a stuck agent now holds the worker, not a terminal. The runner timeout still applies; `advance` has no cancel endpoint in this plan.
- SQLite is a single file in `.factory/`; a corrupt DB loses task history. Old JSON files are kept by migration, but new tasks after the cutover exist only in the DB.
- Local auth is a bearer token on loopback: it stops other web pages and casual local callers, not another process running as the same user that can read `state_dir/token`.

## Residual
- No UI, no streaming/SSE, no cancel, no multi-user or remote access.
- Gate caches, worktrees and eval records stay as directories.

## Open questions
None.
