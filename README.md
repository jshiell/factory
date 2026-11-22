# factory

A personal "agentic software factory": a small Python orchestrator that takes a one-line goal and drives headless
[Claude Code](https://claude.com/claude-code) agents (`claude -p`) through **plan → review → implement → verify → audit → merge**,
with a human approving the plan and the merge (at the terminal, or from GitHub with a 👍 under `factory watch`).

It is a learning project, built plan by plan (`docs/plan-N.md`, one per increment), test-first. The point is to find out how far you
can trust unattended agents when every step is sandboxed, every result is checked by something that is not the agent, and the
only things that need a person are the two decisions that matter: *is this the right plan* and *should this land on main*.

## Layout

| Path | What it is |
|---|---|
| `orchestrator/` | The factory: a Python 3.14 package, CLI `factory`; the standard library plus `httpx2`, `jinja2`, `starlette` and `uvicorn` for the server; ~2000 tests |
| `target/` | A tiny Python project (`calc`) the agents work on. Replace it with any `uv` + `pytest` project |
| `docs/` | One `plan-N.md` per increment (decisions, cycles, reviewer findings) and `spike-findings.md` (what was measured, known gaps) |
| `spike/` | Throwaway probes and end-to-end scripts. Not production code; several make real API calls or need a terminal |

## Requirements

- macOS or Linux (the sandbox is `nono` 0.81 or later). What was verified on each is in [Platform verification](#platform-verification). Windows is refused at startup (`launcher.py`), because factory needs `fcntl`, `pgrep`, `ps` and process groups. WSL is untested.
- `git`, [`uv`](https://docs.astral.sh/uv/), Python 3.14
- the `claude` CLI, logged in (OAuth). `ANTHROPIC_API_KEY` is stripped from the agents' environment on purpose
- `nono` on `PATH`

### Platform verification

Linux was re-measured on 2026-11-18 (nono 0.81.0, kernel 7.0.0-43). macOS was measured earlier, up to about plan 36 (2026-11-11), and has not been re-measured since the changes in plans 49 to 57 and O7. Detail and sources are in `docs/spike-findings.md`.

| What | Linux | macOS |
|---|---|---|
| Unit suite and ruff | CI (`ubuntu-latest`, serial, no `claude` or `nono` installed on the runner) and by hand, 2026-11-18 | Not in CI. Run by hand in the early plans; later runs not recorded |
| Agent confinement under `nono` | Current: plan O7. No read of other worktrees, the repo root, the state dir, `$TMPDIR`, `$HOME` or `~/.ssh`; no write to `~/.cache`. Agents can read and write `~/.claude`, including `.credentials.json` | Older code: plans 6 and 9. `$TMPDIR` was readable and `~/.cache`, `~/.claude.json` and Keychain files were writable, which differs from Linux. Plan O7 not re-run |
| `claude` executable grant | Plan 49 (real install under mise); plan 57 (symlinked file, on a fabricated layout) | Never measured |
| Gate sandbox grant for `uv` | Current: plan 56, with `uv` under mise only | Older mechanism: plan 9 (real `uv`, mise shims, Python prefix, private cache). The plan 56 grant has not been tried |
| Gate network and build hooks | Plans 55 and 56: network works; a `setup.py` entry point and a hatch hook run | Plan 9: a cold gate run needs network (observed, not probed). Build hooks not measured |
| Orphan reaping | Plan O7: SQLite records, real `ps`. A `setsid` grandchild whose parent had already exited survives | Older code: plan 21, file records, end-to-end 15 of 15 (`ps` start time read with `LC_ALL=C TZ=UTC0`). Plan 21 also lists `killpg` after a reaped leader as unverified on macOS. The SQLite records have not been run on macOS |
| Prompt size in `argv` | Measured 2026-11-18: a single argument of 131072 bytes or more fails with `E2BIG`; 131071 runs. Prompts are one argument, so `ClaudeHarness.build` refuses a prompt over 131,071 bytes (`prompt_too_large`, no charge), and the audit shrinks its diff limit to the room the task prompt leaves | Total `ARG_MAX` is 1048576 (plan 3), the figure the diff limit was based on. No per-argument cap measured |
| Server signals (`factory serve`) | Plan 69: SIGTERM and SIGHUP stop `serve` with an SSE stream open (`test_serve_stopping`) | Plan 35 C0: SIGTERM and SIGINT run shutdown; uvicorn ignores SIGHUP, so `serve` installs its own handler |
| Case-insensitive filesystem | Not applicable | Plan 20: `UV.toml` and `.Python-Version` are honoured by `uv`; protected names match case-insensitively. Plan 50: `gate_allow_outside` does not handle it |

## Using it

Run everything from the repo root. `uv run --project orchestrator factory <command>` (or install the `factory` script).

```sh
factory new "add a divide function that raises on zero"   # creates a task, prints its id
factory advance t1        # runs stages until a human is needed: plan, review, then stops at "reviewed"
factory approve t1        # needs a terminal: shows goal, plan, risks, review findings; type y
factory advance t1        # implement, verify (with fix rounds), audit
factory merge t1          # needs a terminal: shows diffstat and audit findings; type y
```

`advance` is safe to re-run: it does whatever the current state calls for and stops at the next human gate, a failure,
or its step cap. Agent spend is capped (see `cap_usd`) and every call is recorded in a ledger.

### Commands

| Command | What it does |
|---|---|
| `new <goal> [--no-grill]` | Create a task (`proposed`). `--no-grill` skips the planner's questions for this task; without it the `grill` setting decides |
| `answer <id>` | Answer a task's open questions (state `open_questions`). Needs a TTY (the web UI page does not). See "Grilling" below; then run `advance` again |
| `list` / `show <id>` | List tasks, or show one task's state, goal, plan, risks, review and audit findings, failure reason, worktree and branch |
| `advance <id>` | Run the next automatic stages until a human gate, a failure or the step cap |
| `approve <id>` / `reject <id>` | Accept or reject a reviewed plan. `approve` needs a TTY (the web UI page does not) and binds to a digest of the plan you saw |
| `merge <id>` | Fast-forward `main` to the audited commit. Needs a TTY; shows what will land first |
| `retry <id>` | Start a fresh task from a failed or audited one (new id, `retry_of` set). See "Watching issues" for the `watch_retry` offer |
| `retry <id> --keep-plan` | Same, but queue it with the plan you already approved, skipping plan and review. Needs a TTY: shows the plan and asks `y` |
| `rebase <id>` | An audited task whose merge was refused because `main` moved: replay it on the new `main` and send it back through verify and audit. See "Watching issues" for the `watch_merge` offer |
| `abandon <id>` | Fail a task that is not running (for example one stuck mid-rebase) |
| `recover <id>` | Mark a task whose driver died (`running`) as failed, charging an assumed cost |
| `clean` | After a preview and confirmation, remove worktrees, gate caches, orphan caches and branches of finished tasks |
| `costs [id]` | Spend by stage and by task, with assumed costs shown separately |
| `init` | Write a commented global `<home>/factory.toml` template and the shared plugins directory. Needs no repo or server; refuses to overwrite an existing file |
| `clone <repo>` | Clone a GitHub repository with `gh repo clone` into `<home>/<name>/repo`. Needs no repo or server. See "Cloning a repo" below |
| `watch [--interval N] [--once] [--allow-public]` | Turn the GitHub issues of a cloned repo into tasks, with approvals taken from reactions. See "Watching issues" below |
| `migrate` | Copy the old JSON tasks and ledger under `<state_dir>` (for in-repo state, `<repo>/.factory`; point `state_dir` at it) into `factory.db`. Refuses a database that already has rows; never deletes the old files |
| `serve [--port N] [--host H]` | Run the local HTTP server (below) |
| `ui [--host NAME]` | Print a one-time login link for the web UI; `--host` puts NAME in the link, for a browser on another machine (see "Web UI" below) |
| `eval-audit` | Run the auditor over 25 labelled changes (about $2) and compare with the last comparable run. See "Evaluating the auditor" below |
| `eval-export DIR [--out DIR] [--with-findings]` | Write a shareable `run.json` and `run.md` for an eval directory. Needs no repo or server |

Exit codes: `0` ok, `1` refused or failed (the reason is printed), `2` usage error (`eval-audit` also uses `3` for an incomplete run).

### Cloning a repo

`factory clone <repo>` clones a GitHub repository with `gh repo clone` (needs `gh` installed and logged in) into `<home>/<name>/repo`, where `<name>` is the repository name.

- **Accepted forms.** `owner/repo`, an https URL or `git@github.com:owner/repo`; other hosts are refused. A bare `owner/repo` follows `gh`'s `git_protocol` setting (ssh or https); a typed URL keeps its protocol; the success line shows the origin that was cloned. A fork gets only `origin`, no `upstream` remote.
- **Where.** Needs no repo or server. It refuses an existing target and the names `_shared` and `factory.toml`. The home is `FACTORY_HOME`, then `home` in the global `<home>/factory.toml`, then `~/.harness` (see Configuration); a project `factory.toml` and `--config` are ignored. A failed clone removes the directories it created.
- **After cloning.** A clone has no `target/`, so set `project_subdir` (a subdirectory; the repo root `"."` is refused) in a `factory.toml` at the clone's root and run `factory` from inside the clone.

### Evaluating the auditor

`factory eval-audit` runs the auditor over 25 labelled changes (default 3 runs each, about $2) and compares with the last comparable run. It exits `1` if a bad change is ever judged pass or a good one is judged fail by a majority, and `3` if the run is incomplete. Results go under `<state_dir>/evals/`, with their own eval directory and lock, so it never reaps the server's agents.

- `--runs N`: runs per case.
- `--only CASE`: one case.
- `--max-usd X`: spend cap.
- `--resume DIR`: continue a run (same `--runs` and cases as the original).
- `--compare DIR|run.json`: compare with a given run, or with a bundle written by `eval-export`.
- `--check-corpus`: run the real gate on every case, in the configured gate sandbox (needs `uv`, the sandbox and network).
- `--stage-config`: run the auditor with the audit stage's config (global prompt, `stage_prompts.audit` and the audit plugins); a resume refuses if that config or a plugin file changed.

`factory eval-export DIR [--out DIR] [--with-findings]` turns an eval directory into `run.json` and `run.md` (written into `DIR`, or `--out`, which must exist; earlier copies are replaced). It needs no repo and no server. The Markdown is generated from the JSON and has no date.

- **In the bundle:** the case table with labels and verdicts, hashes, runs, the audit config, the harness and sandbox names, false-pass and false-fail counts with Wilson intervals, cost and the exit code.
- **Left out:** absolute paths (plugins appear by directory name; two with one name are refused), the appended system prompt (only a 16-character sha256 prefix, enough to tell two prompts apart) and, unless `--with-findings`, the auditor's findings.
- **Findings** are agent text and may quote your code. With `--with-findings` they have control characters escaped and are fenced in the Markdown, but read them before sharing.
- **Refused** (exit 1) when `meta.json` is missing or the corpus changed since the run, because the labels could no longer be trusted.

### Server

`factory serve [--port N] [--host H]` (default `server_port`, 8765) listens on `127.0.0.1` by default and holds the state-dir lock for as long as it runs. It can spend money and merge code, so every request needs `Authorization: Bearer <token>`, where the
token is the file `<state_dir>/token` (created with mode 0600 on first start; a more permissive file is refused). A
`Host` that is not loopback and not in `server_allowed_hosts` (below), or any `Origin` header, is refused with 403 (outside `/ui`, which has its own rules; see "Web UI"), which keeps other web pages and DNS rebinding out.
`--host` (or the repo-only setting `server_host`; the flag wins) takes `127.0.0.1` or `0.0.0.0` and nothing else. A `0.0.0.0` bind prints a warning on stderr and leaves the `serving on http://127.0.0.1:N` line on stdout unchanged. On its own it does not enable remote use: with `server_allowed_hosts` empty, the `Host` rule above still refuses any non-loopback `Host` (403) for the API and for `/ui`, so a request from another machine is refused. The guard was written to keep browsers out, not as a network defence, so the wide bind only exposes the port. The CLI keeps connecting to `127.0.0.1`.

Remote access: the repo-only setting `server_allowed_hosts = ["nas.lan"]` (default empty) lists extra `Host` names the guard accepts, for the API and for `/ui`. The guard accepts a `Host` whose name is loopback or listed; the comparison is exact and case-sensitive (an upper-case `Host` is refused) and the port is ignored. The `Host` must be a name with an optional `:` and ASCII digits (`[::1]` is accepted for loopback); anything else after the name, such as `nas.lan:x:y`, is refused with 403, as is a request that repeats `Host`, `Origin` or `Sec-Fetch-Site`. The API rules do not change: the Bearer token is still required and any `Origin` is still refused. Entry rules, checked at load with the error `server_allowed_hosts '<value>' is invalid: <reason>`: lowercase ASCII labels (`a-z`, `0-9`, `-`) separated by dots, or a full IPv4 address (four octets, 0-255, no leading zeros; a numeric entry such as `1.2.3`, which an earlier version accepted, is now an error at startup); no port, scheme, path, space, wildcard, trailing dot or IPv6 address (write an internationalised name in punycode). `0.0.0.0`, loopback names (`127.0.0.1`, `localhost`) and duplicates are refused. The setting is per-repo only (an error in the global file), and an empty list written in the config is an error, as for other list settings. Each start with the list set prints one stderr line naming the hosts and saying the connection is plain HTTP (the `0.0.0.0` bind variant also says the port is reachable from the network; the `127.0.0.1` variant says only loopback and the listed names are accepted, for a tunnel that keeps the `Host`).

- TLS and the `Secure` cookie flag are unsupported: the connection is plain HTTP and the session cookie has no `Secure` flag. Anyone on the network path can read the Bearer token, the login code and the cookie, and the server can spend money and merge code. Use a VPN or an SSH tunnel, and keep the list empty otherwise.
- Prefer IPv4 literals. A listed name is only as safe as the DNS that resolves it: whoever can re-point it passes the `Host` check, and mDNS or `.lan` names can be spoofed on the local network. Keep entries to names you control.
- Not supported: an HTTPS-terminating proxy, and a tunnel that changes the `Host` or uses a different external port than the one in the login link (the link would not match; paste the code from the link into `/ui/login?code=` by hand).
- Sessions are not tied to a host: a cookie stolen on one name works on every other accepted name and on loopback.
- Remote reads lose the Fetch Metadata refusals (see "Web UI").

Stopping it with SIGINT, SIGTERM or SIGHUP shuts it down; jobs it left unfinished are marked `interrupted` at the next start. When an agent cannot be launched at all (a `runner_error` failure), the exception text is logged as a warning on the server's stderr, with the task id and stage; the task itself only records `runner_error`. A prompt too large to pass (`prompt_too_large`) is logged the same way, with its size and the limit. When an agent's result cannot be parsed (`<stage>_unparseable`), the same stderr gets a warning with the task id, stage, exit code, and the last 2000 characters of the agent's stdout and stderr (escaped, on one line); eval runs are included. That log can contain anything the agent saw or printed, so treat it as sensitive as the worktree.

Reads and quick actions answer at once; `advance`, `abandon`, `recover`, `rebase`, `merge`, `clean` and `eval-audit` return
`202 {job}` and run one at a time, in order. Poll `GET /jobs/{id}`.

| Endpoint | Purpose |
|---|---|
| `POST /tasks {goal}` / `GET /tasks` / `GET /tasks/{id}` / `GET /costs?task_id=` | create and read. A goal over 20,000 bytes is 422; the watcher cuts an issue to fit and ends it with `[issue text truncated]` |
| `GET /tasks/{id}/approval`, `POST .../approve {plan_digest, waive, review_findings?}`, `POST .../reject` | approve a reviewed plan; a stale digest is 409. An optional `review_findings` (the lines `/approval` showed) makes changed findings 409 too |
| `GET /tasks/{id}/merge-preview`, `POST .../merge {audited_sha}` | a stale sha is 409 |
| `GET /tasks/{id}/retry-preview`, `POST .../retry` or `{keep_plan, plan_digest, waive, reinstate}` | fresh or keep-plan retry |
| `GET /clean-preview`, `POST /clean {token}` | the token hashes the preview; a changed preview is 409 |
| `GET /tasks/{id}/questions`, `POST .../answer {round, answers}` | the planner's open questions and your answers (at most 20,000 bytes). A stale `round` or a task not in `open_questions` is 409. `POST /tasks` also takes `grill` (default: the `grill` setting) |
| `POST /tasks/{id}/advance`, `/abandon`, `/recover`, `/rebase`, `POST /eval-audit {runs, max_usd, only, stage_config}` | jobs |
| `GET /jobs`, `GET /jobs/{id}` | job state: `queued`, `running`, `succeeded`, `failed`, `interrupted` |

Errors are `{error, detail}`: 401 token, 403 host or origin, 404 unknown task or job, 409 wrong state or stale preview, 422 bad input.
Every CLI command except `init`, `clone`, `migrate`, `serve` and `eval-export` is a client of this server and needs it running (`no factory server is running` otherwise); it reads the token from `<state_dir>/token`. Each state dir has its own token, so a CLI pointed at a different state dir than the running server is refused with a 401. Long commands wait for their job; Ctrl-C stops waiting and the job carries on. `eval-audit` needs the server too, except `--check-corpus`, which runs locally.

#### Web UI

The server also serves a browser page under `/ui`, read-only except for rejecting or approving a reviewed plan and answering open questions. It is always on and uses the same port.

- `factory ui` prints a login link that works once, within 5 minutes. `factory ui --host NAME` prints the same link with its host replaced by NAME, keeping the link's port (a link with no port gives `http://NAME`), for opening it from another machine. The CLI checks NAME itself: it must be a loopback name or in the CLI's own `server_allowed_hosts`, otherwise it prints a usage error, exits 2 and asks the server for nothing. The CLI's settings may differ from the running server's, in which case the link gets 403. The login code is not tied to a host, so the server is unchanged. Opening it shows a page with a link to the UI and sets an HttpOnly, SameSite=Strict cookie `factory_session_<port>`, scoped to `/ui`. A link-preview tool can use up the code; run `factory ui` again.
- The page at `/ui/` lists tasks newest first, marks the ones waiting on you, and updates live over server-sent events (`/ui/events`). Agent-written text in the list (goal, failure reason, task id, skipped names) is shown with control characters, invisible format characters and lone surrogates escaped, as on the task page.
- Each id in the list links to a per-task page at `/ui/tasks/{id}`. It shows what `factory show` shows (state, goal or prompt, failure reason, plan, open questions, risks and review, audit and waived findings, attempts, denial count, worktree and branch, errors) and marks a task waiting on you. It has no spend (see the costs view); its actions are the Answer, Approve and Reject links below. It updates live over its own stream (`/ui/tasks/{id}/events`), and sends an update only when that task's rendering changed since the last one on that connection, so another task's progress does not touch it. Both pages stream only while their tab is visible, so many open tabs do not use up the browser's connection limit for the server: a hidden or background tab holds no stream and shows the current state when you return to it, which resets any text selection and the scroll of a long block (each return opens a new connection, which always starts with the current state). An unknown or malformed id gives a 404 page that does not echo the id. A task whose stored row cannot be read gives an "unreadable" page (status 500) with no traceback, raw data or id.
- A session unused for 8 hours expires (not configurable); every request the guard authenticates counts as use. An open live-update stream is not cut when its session expires; it ends when the page closes it or the server stops.
- Sessions live in memory, so restarting `serve` logs the browser out: the page should show a "Disconnected" banner (see the last bullet), also when you return to a tab that was hidden during the restart; run `factory ui` again.
- Under `/ui` the `Host` must be loopback or in `server_allowed_hosts` (same rule as the API: exact, case-sensitive, port ignored), an `Origin` must be absent or the server's own (reads; writes have a stricter rule, below), and `Sec-Fetch-Site` must be `same-origin` or `none`. Every UI response carries a strict Content-Security-Policy and `Cache-Control: no-store`. The JSON API stays Bearer-only: a cookie does not open it, and Bearer does not open `/ui`.
- Reject: the page of a task in state `reviewed` shows a "reject this plan" link to `/ui/tasks/{id}/reject`. That confirm page shows the goal and one button, which POSTs (a plain form, answered with a 303 back to the task page). The task becomes `failed` with reason `plan_rejected_by_human`. A rejection costs work: a rejected task is terminal, and `retry` only makes a new `proposed` task from the goal (grill answers are lost); `retry --keep-plan` refuses. Any other state, including a task that left `reviewed` meanwhile, gets a 409 page; a missing, malformed or unreadable id gets the 404 or 500 page. None of these pages echoes the id.
- Answer: the page of a task in state `open_questions` shows an "answer these questions" link to `/ui/tasks/{id}/answer`. That page shows each question and has one text field per question, prefilled with the recommended answer; a blank field takes the recommended answer, as Enter does in `factory answer`. The POST (a plain form, answered with a 303 back to the task page) moves the task to `proposed`. A page opened before a new round of questions arrived gets a 409 "questions changed" page. The form body is parsed by hand (no `python-multipart`): urlencoded only, at most 64 KiB, a repeated `round` or answer field is refused, and a bad form gets a 422 page.
- Approve: the page of a task in state `reviewed` also shows an "approve this plan" link to `/ui/tasks/{id}/approve`. That confirm page shows the goal, plan, risks and the review findings as `factory approve` does (capped, required notes numbered, and the warning when required notes beyond the cap were dropped), and one button. The POST binds to the digest of the plan shown, queues the task (303 back to the task page) and waives nothing: required notes stay in the agent prompt and the audit, except any listed as dropped. The page offers no waiver; waive in the terminal (`factory approve`) or with the JSON `waive` field. A changed plan gets a 409 "plan changed" page, and a task in any other state a 409 page. A missing, malformed or unreadable id gets the 404 or 500 page; none of these pages echoes the id.
- The terminal's TTY rule does not apply to these pages: `factory ui` mints a login code with the Bearer token and no TTY check, as the JSON `approve` and `answer` endpoints have none, so anything that can read the token can also approve through a browser session. This adds no power beyond the token.
- Writes under `/ui` (any method but GET and HEAD) also need an `Origin`: exactly the server's own (`http://<Host>`), or the string `null` together with `Sec-Fetch-Site: same-origin`. UI responses send `Referrer-Policy: same-origin` (it was `no-referrer` before plan 74), so a same-origin form POST should carry its real `Origin`, which the exact-Origin rule accepts; browsers are expected to send no `Sec-Fetch-*` headers to a non-loopback name over plain HTTP (checked once by hand, see plan 74), and the old `no-referrer` would have produced `Origin: null` without them. Cross-origin navigations still send no referrer. An `Origin` naming a different accepted host is refused. The `null` Origin on a write is still accepted only together with `Sec-Fetch-Site: same-origin` (loopback), so a remote `null` without Fetch Metadata gets 403 (fails closed); a `null` Origin on a GET is still refused. A manual check from a second machine over plain HTTP confirmed the reject POST succeeded with the real `Origin` and no `Sec-Fetch-*` headers; that is one browser, not a survey.
- On a remote host, a read with no `Sec-Fetch-Site` counts as same-origin, so same-site and cross-site reads are not refused by Fetch Metadata there; they rest on `SameSite=Strict` and, for writes, the exact-Origin check. Any other service on the same host name (any port) is same-site and receives the `Path=/ui` cookie on its own `/ui` paths.
- `Referrer-Policy: same-origin` still sends the full URL, including a login `?code=`, as the referrer to same-origin requests; the code is single-use and short-lived.
- Reject, approve, answer and abandon check the task's state and write it in one SQLite write transaction (`SqliteTaskStore.update`), so a concurrent write cannot be overwritten: the loser gets the wrong-state error (409). Known limit: the other actions (stage runs, merge, retry, rebase, recover) still load then save, last write wins.
- A database row whose stored `id` differs from its key (only a direct database write makes one) is unreadable: `list` reports its key under `skipped`, `load` fails like any damaged row, and the task page shows the unreadable notice. `factory list`, `factory migrate` and `factory costs` print ids and skipped names with control characters escaped. The old file store (`TaskStore`, used by `migrate` and the eval tools) does not make this check.
- Static files under `/ui/static` need no cookie. The vendored htmx 2.0.11 and htmx-ext-sse 2.2.4 are in `orchestrator/src/factory/web/static` (versions and checksums in `VENDORED.md`).
- Risk: cookies ignore ports, so another server on loopback that your browser visits receives the cookie. HttpOnly keeps page scripts from reading it, but that server can replay the cookie with its own `Host` and `Origin` headers, so it could reject or approve a reviewed task (approving queues work that spends money and later merges code) or answer a task's questions. This is accepted for a single-user machine.
- Answer, approve and reject are the only actions so far; merge, abandon, retry and waivers stay in the terminal.
- Not yet checked in a real browser (none was available for plans 69 and 70, plan 74's remote sign-in is unchecked until its manual check, and plan 75's manual check is pending): the banner, the task page's layout and live update, the reject button, which `Origin` and `Sec-Fetch-Site` headers browsers send, and streaming only in visible tabs (closing on hide, reopening on return and after Back or Forward). The guard rules and the stream are covered by tests; the visible-tab script is not run by any test.

### Watching issues

`factory watch [--interval N] [--once] [--allow-public]` turns issues into tasks and takes approval from GitHub reactions, using the `gh` CLI. State lives in `<state_dir>/watch.json`.

- **Running it.** Watch a repo cloned with `factory clone` (see "Cloning a repo"). It needs `gh` logged in, a running `serve`, and `watch_approvers` in the clone's `factory.toml`. An issue opened by an approver becomes a task and is advanced to a reviewed plan, which is posted on the issue. It polls every `--interval` seconds (default 60); `--once` runs a single pass. One watcher per state directory. Every advance's outcome is posted, with `factory merge <id>` for the human step unless the opt-ins below apply.
- **Who counts.** Only issues opened by, and reactions made by, accounts in `watch_approvers`, compared by numeric GitHub id (resolved once at start). Issues created before the first run are ignored.
- **Plan comment.** The plan, review findings and required notes are posted inside code fences, so `@mentions`, issue links and images in them stay inert. Each watcher comment starts with an HTML marker line; after a restart only marker comments by the watcher's own account are adopted.
- **Questions.** When the planner has questions (see "Grilling" below) the watcher posts them as a comment, in a code fence, with instructions. Reply in the issue: comments from approvers after the questions comment are joined and sent as the answers (cut at 20,000 bytes), and the task moves on. A reply that starts with a factory marker is ignored. If the questions comment is deleted the watcher posts a stopped result; answer at the terminal with `factory answer <id>`. A later round posts a new questions comment.
- **Approval.** An approver's 👍 approves with the digest posted, keeping required review notes (waiving stays terminal-only). Before approving, the watcher checks the plan comment, plan digest and findings are unchanged; if not, it posts once and stops (`changed`). It also sends the findings it checked with the approve request, so the server refuses (409) if they changed in between. A 👎 from an approver rejects, and 👎 wins over 👍.
- **Merge (opt-in).** With `watch_merge = true` (repo-only, default false) an audited task gets a result comment and then a merge comment: task id, audited commit, branch, diffstat and audit findings, all in code fences. Tasks already audited before you turned this on get no retroactive merge comment. With `watch_merge` off, the result comment only names `factory merge <id>`.
  - **Approving.** An approver's 👍 on the merge comment merges that exact commit (`merge` is sent the audited sha, so it refuses if the branch moved); 👎 declines, and 👎 wins over 👍. Before merging the watcher checks the merge comment is unedited and a fresh preview shows the same audited commit; if not it posts `merge-changed` and stops.
  - **Main moved.** A merge refused because `main` moved posts `merge-moved` instead: the reason and the audited commit, and an approver's 👍 rebases the task onto `main` (👎 declines and posts `not-merged`, and 👎 wins). After the rebase the watcher carries on: the task is verified and audited again and a new merge comment follows, which needs its own 👍; the first audit's result comment is reused, so its steps are stale. If the task has already left the offered commit, no second rebase runs.
  - **Refusals.** A failed rebase (a conflict, a dirty worktree) posts `rebase-refused` and stops; resolve it at the terminal. Any other refusal (a dirty checkout, not a fast-forward) posts `merge-refused` with the reason; a job interrupted by a server restart is retried on the next pass.
  - **Landing.** A successful merge posts `merged`; the commits are unsigned, so sign them before pushing (the watcher never pushes). After `merge-changed`, `merge-refused` or `not-merged` the task stays `audited` and the watcher leaves it alone: there is no way back in from GitHub, so finish at the terminal (`factory rebase <id>`, then `factory merge <id>`; a task rebased at the terminal gets a new merge comment after its re-audit).
- **Retry (opt-in).** With `watch_retry = true` (repo-only, default false) a task that fails gets its failure comment and then a `retry-offer` comment: the task id, the failure reason in a code fence, and a note that a retry spends agent plan and review budget. An approver's 👍 starts a fresh task from the same goal (the goal stored on the failed task, not the issue's current text, so an edited issue is not picked up) and the watcher follows the new task on the same issue; its plan is posted as usual and needs its own 👍. 👎 declines (`not-retried`, and 👎 wins over 👍). A server refusal posts `retry-refused` and stops; finish at the terminal with `factory retry <id>`. Every failed task gets the offer, including one a human stopped at the terminal. Tasks that failed before you turned this on get none. A `retry` at the terminal does not withdraw the offer, so a later 👍 would start a second retry. Not offered for audited or stopped tasks, and `--keep-plan` stays at the terminal. If the task or the offer comment is deleted, every pass retries and logs the error.
- **Public repos.** Refused unless `--allow-public`: plans, failure reasons and worktree paths would be public.
- **Trust.** Whoever controls an approver's GitHub account can approve plans, and with `watch_merge` on, put code on `main` and trigger agent spend by rebasing, and with `watch_retry` on, trigger agent spend by retrying a failed task (the new plan still needs its own 👍). The `gh` token must not be readable by the agents the factory runs (check this under your sandbox profile before pointing `watch` at a real repo). Agents and the sandboxed gate inherit the factory's environment except `VIRTUAL_ENV`, `GIT_DIR`, `GIT_WORK_TREE`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN` and `GITHUB_ENTERPRISE_TOKEN`. Other credential variables still reach them, and so do the GitHub tokens in a gate run without the sandbox, so keep secrets out of the environment you start the factory from. A write-access collaborator can edit an issue body before the first poll. Each path in `gate_allow_outside` lets an agent change it unchecked by the gate, so list only files nothing reads when the tests run.
- **Serial.** One pass handles issues one at a time, so a long advance delays a 👍 on another issue. A merge or rebase waits for the server's single job worker, so a pass can stall for minutes behind a terminal job. A failure on one issue is logged and retried on the next pass. The merge fast-forwards the server's checkout of `main`, which may be your working copy: tracked changes refuse it, and untracked files that collide make it fail.
- **Not covered.** Webhooks, other reactions, several repos, re-planning on issue edits, and every terminal-only command other than `merge`, the `main`-moved `rebase` and a plain `retry` of a failed task (`retry --keep-plan`, `retry` of an audited task, `abandon`, `recover`, `clean`, waiving review notes, a rebase conflict).

### Grilling

Before writing a plan the planner can ask you questions, using the shipped `grilling` plugin (loaded for the plan stage automatically; it runs no code). The task then waits in `open_questions` until you answer (`factory answer <id>`, or in the issue under `watch`). `factory answer` shows each question with the planner's recommendation; Enter takes the recommendation and Ctrl-D answers nothing. Each answer round can lead to another, up to `grill_max_rounds` (default 3); after that the planner must plan. Settings: `grill = true` (default; `--no-grill` or `"grill": false` overrides per task) and `grill_max_rounds = 3` (`0` turns grilling off). Cost: up to `grill_max_rounds + 1` planner calls per task instead of one.

No downgrade: an older build reading a `watch.json` or task rows written with the new fields raises a `TypeError`, and one reading a new phase (such as `awaiting_merge` or `awaiting_retry`) raises `WatchStateError`.

### Configuration

Optional `factory.toml` in the working directory (or `--config <file>`). Relative paths resolve against the file; `~` is expanded in `home`, `state_dir` and `worktree_root`. Unknown keys are an error.

State and worktrees live outside the repo, one subdirectory per project: `<home>/<project>/state` and `<home>/<project>/worktrees`, where `<project>` is the repo directory's name (resolved, so symlinks and `..` follow the real directory). The home is, in order: the `FACTORY_HOME` environment variable (absolute, `~` allowed; empty is ignored), `home` in `factory.toml`, then `~/.harness`. A repo at `<home>/<project>/repo`, as made by `factory clone`, is keyed by `<project>` instead of `repo`. Two other repos with the same directory name share a project directory; give one of them its own `home` or `state_dir`. Worktrees are no longer beside the repo's `.git`. An empty `HOME` is rejected when the default home is needed (set `FACTORY_HOME` or `home`); a `~` in a config path with an empty `HOME` still expands to `/`.

Global settings: an optional `<home>/factory.toml` (the `FACTORY_HOME` or `~/.harness` home; the `home` key cannot move it) applies to every project. The project's `factory.toml` or `--config` file overrides it key by key, except `stage_prompts`, which merges per stage (`plugins` and each stage's `stage_plugins` list are replaced as a whole, so a global prompt can outlive a plugin the project list dropped). Each file's relative paths resolve against its own directory. `repo`, `state_dir`, `worktree_root`, `main_branch`, `server_host` and `server_allowed_hosts` are per-repo and are an error in the global file; errors from it name the file. Startup only reads it; `factory init` writes a commented template there, needs no repo or server, and refuses to overwrite an existing file. There is no switch to skip the global file; pointing `FACTORY_HOME` at an empty directory does, but it also moves state and worktrees.

Plugins: `plugins = ["name", ...]` enables plugin directories at `<home>/_shared/plugins/<name>` (created by `factory init`) for every agent stage and the fixer; `[stage_plugins]` (`plan`, `review`, `implement`, `audit`) adds more to one stage, after the global ones, without duplicates. The fixer shares `implement`. Both work in the global and project files; a project list replaces the global one (for `stage_plugins`, per stage). A plugin can carry skills and agents (`<name>:<skill>`, `<name>:<agent>`). Each stage gets `--plugin-dir` for its plugins and the sandbox a read-only grant for just those directories. Default: none; `eval-audit` ignores them unless run with `--stage-config`. A missing directory or a bad name is a settings error. Plugins are trusted code you write: the stages' tool deny lists reach plugin agents and subagents, but plugin hooks run outside them, so a plugin with a `hooks/` directory, a `.mcp.json`, or `hooks` or `mcpServers` in its `plugin.json`, or a `hooks:` key in the frontmatter of a skill or agent, or a `plugin.json`, `SKILL.md` or agent file that cannot be read, is refused (best effort; other places that could run code are not checked). No `allowed_tools` change is needed. `Agent` was already callable in the read-only stages. `<home>/_shared` would collide with a repo named `_shared`.

Upgrading from in-repo state: move `<repo>/.factory` to `<home>/<project>/state`, and keep `worktree_root = ".worktrees"` in `factory.toml` until the old tasks are cleaned (otherwise `clean` skips them as `outside_root`). Nothing is migrated or refused automatically; an old `.factory` is silently ignored.

```toml
repo = "."                     # default: the git toplevel
home = "~/.harness"            # default; FACTORY_HOME overrides it
state_dir = "..."              # default: <home>/<project>/state (database, token, gate caches, lock)
server_port = 8765             # where `serve` listens and the CLI connects
server_host = "127.0.0.1"      # repo-only; "127.0.0.1" or "0.0.0.0" (`serve --host` overrides). A wide bind alone does not enable remote use: non-loopback Hosts are refused unless listed in `server_allowed_hosts`
server_allowed_hosts = ["nas.lan"]  # repo-only, default empty: extra Host names the guard accepts (see "Server"); plain HTTP, use a VPN or SSH tunnel
worktree_root = "..."          # default: <home>/<project>/worktrees (one git worktree per task)
project_subdir = "target"      # where the agent works and the tests run; a subdirectory, never the repo root ("." is refused at load: set a subdirectory instead). Edits outside it are refused unless listed in gate_allow_outside
gate_allow_outside = ["README.md", "docs"]  # default unset; repo-root-relative files or directories a task may also change (see the gate section)
gate_allow_dependency_changes = false  # default false; repo file only. true lets a task change dependencies, entry points, build config and uv.lock (see the gate section)
grill = true                   # the planner may ask questions first
grill_max_rounds = 3           # answer rounds before the planner must plan; 0 = no grilling
watch_approvers = ["alice"]    # repo-only; GitHub logins whose issues and 👍/👎 `factory watch` obeys (required for `watch`)
watch_merge = false            # repo-only; true lets an approver's 👍 on the merge comment merge an audited task, and on a merge-moved comment rebase it (spends agent budget)
watch_retry = false            # repo-only; true lets an approver's 👍 on a retry offer retry a failed task (spends plan and review budget)
main_branch = "main"           # repo-only; default unset (no check). When set, merge and rebase refuse unless the server checkout is on this branch; read at `serve` startup, so restart after changing it
test_argv = ["uv", "run", "pytest", "-q"]
model = "sonnet"
budget_usd = 0.5               # per implementer call
cap_usd = 5.0                  # spend cap across all tasks (the whole ledger)
max_turns = 30
fix_rounds = 2                 # how many times the implementer may be asked to fix a failing gate
gate_timeout_s = 120
runner_timeout_s = 300
harness = "claude"             # the agent CLI adapter; "claude" is the only one built in (see factory/harness.py)
claude_executable = "claude"   # the program the harness starts, whatever the harness
sandbox = "nono"               # the confinement mechanism; "nono" is the only one built in (see factory/sandbox.py)
# sandbox_executable = "/path/to/nono"   # optional; unset means the sandbox's own default
allowed_tools = []
plugins = []                   # plugin directory names under <home>/_shared/plugins
append_system_prompt = "..."   # optional; appended to every stage's system prompt

[stage_prompts]                # optional; appended for that stage only (plan, review, implement, audit)
plan = "If GLOSSARY.md or docs/adr exist, use their terms and respect the ADRs."
review = "If GLOSSARY.md or docs/adr exist, use their terms and respect the ADRs."
implement = "Test behaviour through the public interface, not private helpers or call order. Keep each test to one behaviour and name it for that behaviour."   # the implementer only; the fixer gets the global prompt and the implement plugins, not this
```

These hints are examples, and none of them has been shown to improve output. A hint that names a skill the stage does not have costs a turn (`Unknown skill`). A project-level `plugins` or `stage_plugins` list replaces the global one while `stage_prompts` merge, so a global hint naming a skill can outlive the plugin the project list dropped; keep a skill's hint next to the list that enables it. In a spike (`docs/spike-findings.md`, plan 41) a hint that named the `codebase-design` skill did not make any plan, grill or review run call it; a stronger hint that required it did on 3 of 5 paths, but not reliably, and the skill's `DEEPENING.md` could not be read in a stage. So no stage's example enables it.

Appended text follows the built-in prompt: built-in, then `append_system_prompt`, then the stage's entry, separated by blank lines. Values must be non-empty strings; an unknown stage name is an error. `eval-audit` ignores both unless run with `--stage-config`, so its runs stay comparable. To compare an audit prompt or plugin, run both evals with `--stage-config`, change only the `audit` plugin list and prompt between them, and restart `serve` in between (settings are read once at startup); `--compare` then prints `AUDIT_CONFIG differs`, which is expected.

The `harness` setting names the agent CLI adapter, nothing to do with the harness home (`~/.harness`, the `home` key). Only `claude` is built in. An unknown name is a settings error. A harness must provide every capability the stages use (JSON-schema output, plugins, a budget, tool rules), or startup refuses it with a settings error and exit code 2. That check runs while the context is built, so every command that reads the repo (`list` and `show` included) fails the same way, not just the ones that start an agent. `eval-audit` records the harness in its meta, refuses to `--resume` a run made with another one, and `--compare` prints `harness differs` when the two runs used different harnesses; a meta without one reads as `claude`. It does the same for the sandbox: the meta records the sandbox name, `--resume` refuses another, `--compare` prints `sandbox differs`, and a meta without one reads as `nono`.

The `sandbox` setting names the confinement mechanism for agent runs and the gate; only `nono` is built in, and an unknown name is a settings error. `sandbox_executable` is optional and selects the binary through the named sandbox; when unset, each sandbox picks its own default (`nono` for nono). Eval meta and bundles record only the sandbox's name, not its version, profile, grants or `sandbox_executable`, so the same name is not proof of the same confinement. The policy also names the directory the process is launched in (`cwd`); nono ignores it. What a sandbox grants is what runs today: for an agent run the harness's profile, the working directory (writable; nono's `--allow-cwd` grants it, not the profile), the worktree read-only, and read access to the plugin dirs and the agent's directory; for the gate, write access to the project dir and a private uv cache and read access to the worktree, Python and uv (uv's directory is not granted if it holds `$HOME` or the state dir). Network is open in both (nono allows it by default and neither prefix passes `--block-net`).

## Design

### Task lifecycle

Every task is a row in the state directory's SQLite database; all changes go through one state machine (`task.py`), so an
illegal move is an error, not a bug waiting to happen.

```mermaid
stateDiagram-v2
    [*] --> proposed: new
    proposed --> planned: plan agent (read-only)
    proposed --> open_questions: planner asks
    open_questions --> proposed: answered
    planned --> reviewed: review agent (read-only)
    reviewed --> queued: approve (human, TTY)
    reviewed --> failed: reject (human)
    queued --> running: implement
    running --> agent_done: agent finished
    agent_done --> verified: gate passes
    verified --> audited: audit passes
    audited --> merged: merge (human, TTY)
    audited --> agent_done: rebase (main moved)
    proposed --> failed
    planned --> failed
    running --> failed
    agent_done --> failed
    verified --> failed
    merged --> [*]
    failed --> [*]
    note right of failed
        failure_reason says why.
        abandon: any non-running state.
        recover: running (driver died).
        retry: starts a new task.
    end note
```

### Pipeline and who decides what

The agents propose; deterministic code and a human decide.

```mermaid
flowchart LR
    goal([goal]) --> plan["plan<br/>read-only agent"]
    plan --> review["review plan<br/>read-only agent"]
    review --> approve{{"approve<br/>human"}}
    approve --> impl["implement<br/>agent in its own worktree<br/>no Bash, no git"]
    impl --> gate["gate<br/>commit, clean checkout,<br/>tamper checks, tests<br/>under a sandbox"]
    gate -- "tests fail" --> fix["fix round<br/>(up to fix_rounds)"]
    fix --> gate
    gate -- pass --> audit["audit<br/>read-only agent<br/>reviews the diff"]
    audit -- pass --> merge{{"merge<br/>human"}}
    merge --> main[(main, fast-forward only)]
    gate -- fail --> failed([failed])
    audit -- fail --> failed
    merge -. "main moved<br/>(audited commit not in main)" .-> rebase["rebase<br/>human-run"]
    rebase --> gate
```

### Components

```mermaid
flowchart TB
    cli["cli/<br/>commands, TTY prompts, exit codes"]
    advance["advance.py<br/>state → next stage"]
    stages["stages.py<br/>wires settings into stages"]
    subgraph stage_fns [stages]
        plan_task
        review_plan
        run_task
        verify_task
        audit_task
    end
    task["task.py<br/>state machine"]
    store["sqlite_store.py<br/>one row per task, in factory.db"]
    runner["runner.py, harness.py, sandbox.py, command.py (RunnerConfig)<br/>build and run `claude -p` under nono"]
    gate["gate.py, gate_sandbox.py,<br/>worktree_clean.py<br/>the trusted checker"]
    wt["worktrees.py<br/>git worktree per task, hardened git"]
    ledger["ledger.py, step.py<br/>spend cap, cost accounting"]
    human["merge_task.py, rebase_task.py,<br/>clean_tasks.py, retry/abandon/recover"]

    cli --> advance --> stages --> stage_fns
    cli --> human
    stage_fns --> task --> store
    stage_fns --> runner
    stage_fns --> ledger
    verify_task --> gate
    stage_fns --> wt
    human --> task
```

### How agents are kept in check

- **Sandboxed.** Every agent runs under `nono`. Measured on Linux (nono 0.81, `spike/confinement_probe.py`; for macOS see [Platform verification](#platform-verification)): it can read and write its project directory, and read the directory holding the executable. Other tasks'
  worktrees, the main checkout, `.git/config`, the state directory and the rest of `$HOME` were denied. Not denied: the
  agent can read and write `~/.claude` (including the credentials `claude` itself needs) and write `$TMPDIR`. The GitHub token
  variables are removed from its environment (and the sandboxed gate's). The implementer has no `Bash` and no `git`; plan, review and audit
  agents have no write tools, which is a tool restriction, not nono: their sandbox still lets them write the project directory.
  Each agent is also granted read on the directory `claude_executable` is found in (`dirname` of `which`, looked up on each run with the agent's `PATH`), because nono refuses to start a program whose directory it cannot read. If the executable file is itself a symlink into another directory, that resolved directory is granted too (plan 57; see [Platform verification](#platform-verification)). Each directory is checked on its own and skipped when it is `/`, `$HOME` or contains the state directory (it holds the server token). A relative `claude_executable`, or a mise shim as the executable, is not supported: use the real binary's path. The gate's test run gets the same kind of grant: read on the resolved directory holding `uv`, skipped when that is `/` or contains `$HOME`.
- **Reviewer notes.** The plan review's findings (capped: 10 items, 500 characters each, about 4000 in total, one line each) are shown at `approve` and sent to the implementer and the fixer as advisory notes. `approve` and `retry --keep-plan` refuse if the stored findings differ from the ones shown.
  - **Required and optional.** The review prompt asks for edge-case gaps (concrete inputs, without changing the verdict), so the notes now usually name gaps like bytes, overflow and NaN. Each gap ends with "Recommend (required): " or "Recommend (optional): " and an imperative sentence. The reviewer uses required for a silently wrong result (including nan or inf), a crash or a hang on an input the signature admits, and optional for precision, style and documentation. The heading in the prompt tells the implementer, fixer and auditor that required notes must be handled (by the recommended behaviour or an equivalent, with a test) and optional ones may be skipped; the auditor fails a change that leaves a required note unhandled. Untagged notes (older tasks) keep the old meaning.
  - **Waiving.** Tagging is model judgement, a required note can be technically wrong (for example `math.fsum` does not avoid overflow) and can be waived by you: at `approve` and at `retry --keep-plan` the required notes are numbered (`- [1] ...`), and answering `w 1 3` (then `y` to a second question that lists them) drops those notes from every agent's prompt and records them in the task's `waived_findings`; only required notes can be waived.
  - **Undoing a waiver.** A waiver can be undone only at `retry --keep-plan`: the notes waived earlier are listed again as `[r1] ...` and answering `r 1` (then `y`) sends that note to the agents and the audit again and takes it off the waived list; `r` and `w` can't be mixed in one answer.
  - **Caps.** A required tag is kept when a long note is cut at 500 characters; required notes are kept before any other note when the caps apply: they are exempt from the 4000-character total (so the notes can reach about 5000 characters) and only the 10-item cap can drop one, so more than ten required notes lose the later ones, marked by `[truncated]`, and `approve` and `retry --keep-plan` print a `WARNING: N required note(s) beyond the first 10 were dropped ...` line before the prompt so you can reject and re-plan. Other notes fill what is left.
  - **Audit findings on retry.** `retry --keep-plan` of a task the audit rejected also sends the audit's findings to the new attempt (shown first as `AUDIT FINDINGS (sent to the next attempt)`; bounded, one line each, fenced as untrusted data; a later kept-plan retry of that attempt inherits them until a fresh audit rejection replaces them; a finding that quotes a note you waived is dropped), and the fixer gets the same carried gaps in its fix prompt under its own heading (context while fixing the failing tests, not a task).
- **The agent never commits and never judges its own work.** The factory commits (unsigned, factory identity, hooks off) and
  then runs the gate itself.
- **The gate is a clean checkout.** Before the tests run, the worktree is committed and cleaned (`git clean -ffdx`), so only
  committed files can influence the result. Git itself is run with `fsmonitor`, hooks and submodule tricks disabled, and a nested
  `.git` anywhere under the project is refused.
- **The factory's git ignores your git config.** An agent can write a `.gitattributes` that selects a filter, textconv or
  merge driver your own config defines (git-lfs, say), and the factory's `git add -A` or `status` would run it (measured with
  fixture drivers, `spike/git_hardening_probe.py`). So every git call the factory makes reads no global or system git config and drops
  `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS` and `GIT_CONFIG_KEY_*`/`VALUE_*`, including the merge into your main checkout. The one
  exception is creating a worktree, which checks out your own committed tree with your config. The repo's own `.git/config` still
  applies (an agent cannot write it). Consequences: repos that need clean/smudge filters (git-lfs, git-crypt, `ident`) are not
  supported; a global `core.excludesFile` is not honoured (put ignores in the repo's `.gitignore`); a repo that needs a global
  `safe.directory` fails after the worktree is created; and git 2.32 or later is needed (older git ignores `GIT_CONFIG_GLOBAL`).
- **Tamper checks.** Existing tests may be added to but not removed, shrunk, renamed or shadowed (a duplicate `def test_x` that
  disables the first is caught). Protected config (`conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `uv.toml`, `.python-version`, matched case-insensitively; the pytest and `[tool.uv]` tables of any
  `pyproject.toml` from the repo root down to the project) may not change (reason `tests_modified`). So may not anything that adds code or changes what is installed: `setup.py` and `hatch_build.py`
  anywhere, a change to an existing `uv.lock` (a newly added one is fine), and every part of a `pyproject.toml` in or above the project, or below it, except plain metadata
  (`name`, `version`, `description`, `readme`, `authors`, `maintainers`, `license`, `license-files`, `keywords`, `classifiers`, `urls`, `requires-python`; and the `ruff`, `mypy`,
  `black`, `isort`, `pyright` and `coverage` tool tables). That covers dependencies, dependency groups, entry points, scripts, `[build-system]` and any other `[tool.*]` table.
  Set `gate_allow_dependency_changes = true` in the repo's `factory.toml` to allow all of that; `[tool.pytest]`, `[tool.uv]` and the protected names stay frozen. The setting is refused
  in the global config. The agent prompts say what the gate refuses, and the planner and reviewer say a plan needing a new dependency will be refused. Every change must be inside the project subdir:
  anything outside it (root config, `sitecustomize.py`, a nested repo) fails the gate as `outside_project`.
- **Allowed outside paths** (`gate_allow_outside`, project or global file; a project list replaces the global one). Literal
  repo-root-relative files or directories a task may also change, e.g. a root `README.md` or a `docs` tree. No globs; a directory
  covers everything under it. Protected config under an allowed directory still fails `tests_modified`, and a symlink or gitlink
  a task adds or changes under an allowed path fails `outside_project` (an unchanged one is tolerated). Settings reject an
  empty list, duplicates, the project itself or anything inside or above it, `uv.lock`, `.gitattributes`, `.gitmodules`, any
  `.git` component and the protected names. A task that changes only allowed paths counts as a change: the tests then run on an
  unchanged project, so "verified" says nothing about those files, and a fix round that changes only them re-runs the same
  failing tests (bounded by `fix_rounds`). Do not list anything the tests read (a doctest glob, `--doctest-glob`, a test that
  opens the file, a uv workspace member): the gate cannot tell, and the agent could change what the tests see. The implementer
  and fixer prompts name the allowed paths when the setting is on.
- **Tests run sandboxed too**, with a private per-task `uv` cache and a filtered environment. A failing gate triggers up to
  `fix_rounds` fix rounds; a frozen reference prevents the fixer from rewriting tests it was already judged against.
- **Audit.** A separate read-only agent reviews the diff against the plan and returns a structured verdict. It is a second opinion,
  not a proof. Measured on hand-written cases (plans 22 to 24, rerunnable with `factory eval-audit`) it failed every bad change in every run (14 of 14) and passed all 10 clean ones, but the cases were small and written by the auditor's author; large and ambiguous diffs are untested. The auditor reads the reviewer notes as part of the task: it failed a change that ignored an imperative note (case-21) and a change that skipped required notes (case-24), and passed changes that skipped only untagged or optional notes (case-22, case-23, case-25 and real `mean` tasks). It sometimes calls an untagged imperative note \"required\" in its text (case-21), though the verdict is the same.
- **Humans hold the two irreversible steps.** `approve` binds to a digest of the plan shown; `merge` shows the exact diff and only
  fast-forwards to the exact audited commit. Both refuse without a TTY, so an agent or a script cannot approve itself. That rule is CLI-only: the JSON API and the web UI pages
  approve for anyone holding a Bearer token or a `factory ui` session.
  `factory watch` deliberately weakens this for `approve`: the server has no TTY check, so an allowlisted GitHub user's 👍 approves
  without one. Anyone who can act as an approver's GitHub account, or who can read the `gh` token, can approve; see "Watching issues".
  `merge` stays terminal-only unless you set `watch_merge = true`, which lets an approver's 👍 on the merge comment merge the
  audited commit the same way; it is off by default and per-repo only.
- **Moved main.** `merge` never merges; if `main` moved it refuses. `rebase` replays the audited commits and returns the task to
  `agent_done`, so it must pass the gate and a fresh audit again before `merge` will accept it. `rebase` grants no authority,
  so it needs no TTY, but it is never called by `advance`. With `watch_merge` on, a merge refused only because `main` moved
  posts a `merge-moved` comment, and an approver's 👍 on it runs `rebase` (it spends verify and audit budget, lands no code); the
  re-audit then gets its own merge comment and 👍. With `main_branch` set, `merge` and `rebase` refuse
  ("main is on branch X, not Y") when the server checkout is on another branch. With it unset, `rebase` follows the server
  checkout's HEAD, not necessarily `main`: if that checkout is on another branch, the refusal appears and the 👍 rebases onto
  that branch. The check compares names only (it does not verify the branch exists). `rebase` also refuses when that
  checkout is detached ("main is not on a branch") or has uncommitted changes to tracked files, as `merge` does. If `main` has moved past the audited
  commit (it is already in `main`), the preview refuses with "already in main" and the watcher posts `merge-refused` with no
  rebase offer. If `main` is exactly at the audited commit (a merge that crashed before saving), the preview passes and `merge`
  records the task as merged.
- **Money.** Every call is metered into the ledger table of `factory.db`; a global cap (the sum over every task) stops further calls. Interrupted calls are charged an
  assumed cost so a crash cannot hide spend.
- **One driver at a time.** A lock in the state directory stops two factory processes mutating tasks concurrently; killing the
  driver (Ctrl-C, TERM, HUP) kills the agent tree. After a SIGKILL the next `factory serve` finds the agents and
  gate runs it recorded in the `processes` table of `factory.db` and kills them first; if it cannot prove them dead (for example `ps` is blocked)
  it refuses and changes nothing.

### Why this shape

- *A SQLite file and a state machine* so everything is inspectable and a crashed run is recoverable by hand.
- *Few dependencies* in the orchestrator (`httpx2`, `jinja2`, `starlette`, `uvicorn`, only for the server and its client), to keep the trusted core small.
- *One git worktree and branch (`factory/<id>`) per task* so agents never touch each other or the main checkout, and a rejected
  task is just a directory to delete (`clean`).
- *Every stage is a function of the task file* (`task_id → Task`), which makes each stage testable with fakes and `advance` a loop.

## Development

```sh
cd orchestrator && uv run ruff check     # lint (E, F, I, B, UP, C4, SIM, RUF); CI runs this on every push
cd orchestrator && uv run pytest -q -n auto  # ~2000 tests, real git in temp repos, ~25s in parallel; CI runs them serially on Linux
cd orchestrator && uv run pytest -q -m smoke  # 25 tests, ~3s: one or two per core seam, for a quick check while editing; not a substitute for the full run
cd target && uv run pytest -q
```

Work follows the plan-per-increment flow in `docs/`: spike → `plan-N.md` → independent plan review → red/green/commit per behaviour.
Anything that needs a terminal, nono, the network or the home directory is covered by a throwaway script in `spike/` that you run by hand.

## Known limits

Recorded per plan in `docs/spike-findings.md`. The main ones, by area:

**The gate**
- A new test file can monkeypatch the code under test at import time; the gate cannot tell.
- With `gate_allow_dependency_changes` on, a new dependency or entry point can run code in the gate. With it off, a malicious release of an
  already-pinned dependency within its allowed range is still not caught.
- `setup.py`, `dynamic` metadata and poetry or hatch tables are refused. On Linux (plan 55) a `setup.py` entry point and a hatch build hook were both seen to run in the gate; poetry hooks were not tested.
  On Linux the gate sandbox has network, and `uv run` left an old-format `uv.lock` untouched. For macOS see [Platform verification](#platform-verification).
- Legitimate edits outside the project (a root README, root config) are refused and need a human, unless the path is listed in `gate_allow_outside`.
  The gate cannot know which allowed files the tests read, and a task that changes only allowed paths is "verified" on an unchanged project.
- Submodules are only partly handled.

**The audit and rebase**
- The auditor is a model, not a guarantee (see plan 22 for what was measured).
- A rebase can change behaviour without a textual conflict; only the tests and the fresh audit catch that.

**Operations**
- A SIGKILLed driver leaves an orphan agent until the next `factory serve` reaps it (one launched in the last instant before the kill is unrecorded; a process that called `setsid` and whose parent had already exited survives the reap, measured on Linux), and can leave a half-done rebase; `abandon`/`recover` and a manual check are the way out.
- A prompt of 131,072 bytes or more cannot be passed (Linux limits one `argv` string; see [Platform verification](#platform-verification)). It is refused as `prompt_too_large` without a charge. Every stage but the audit fails the task with that reason (`fix_prompt_too_large` for the fix stage), since a retry would build the same prompt. A goal is capped at 20,000 bytes so the plan and review prompts always fit; the implement, fix and grill prompts can still overflow, because they also carry the plan, notes or answers. The audit refuses (`diff_too_large`) a diff over 200,000 bytes, or over whatever room the task prompt, which holds the whole plan, leaves under that limit.
- The sandbox was re-measured on Linux only (nono 0.81, plan O7 probes), by a human running the spike scripts; CI covers the Linux suite with fakes, not nono itself. For macOS see [Platform verification](#platform-verification).
- The gate sandbox grants read on the resolved directory holding `uv` (plan 56), without which the gate's test run exited 127 when `uv` lives under mise. Confirmed on Linux by the smoke and full probe runs (nono 0.81, uv 0.13.8, uv under mise only). Not covered: a relative `uv` path gets no grant and still exits 127; other layouts (system path, `~/.local/bin`) were not tried (for macOS see [Platform verification](#platform-verification)); the state directory is not refused (only `/` and `$HOME`).
