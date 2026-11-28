# HTTP API

The JSON API served by `factory serve`. The Bearer token, the `Host` and `Origin` guard and the bind rules are in the
README, under [Remote access and security](../README.md#remote-access-and-security); the web UI at `/ui` is described
under [Web UI](../README.md#web-ui).

Reads and quick actions answer at once; `advance`, `abandon`, `recover`, `rebase`, `merge`, `clean` and `eval-audit` return
`202 {job}` and run one at a time, in order. Poll `GET /jobs/{id}`.

Every route below sits under `/repos/{name}` (for example `GET /repos/proj/tasks`). `GET /repos` lists the served repos, `POST /ui-login-codes` is server-wide, and `/jobs` lists the jobs of one repo.

| Endpoint | Purpose |
|---|---|
| `POST /tasks {goal}` / `GET /tasks` / `GET /tasks/{id}` / `GET /costs?task_id=` | create and read. A goal over 20,000 bytes is 422 |
| `GET /tasks/{id}/approval`, `POST .../approve {plan_digest, waive, review_findings?}`, `POST .../reject` | approve a reviewed plan; a stale digest is 409. An optional `review_findings` (the lines `/approval` showed) makes changed findings 409 too |
| `GET /tasks/{id}/merge-preview`, `POST .../merge {audited_sha}` | a stale sha is 409 |
| `GET /tasks/{id}/retry-preview`, `POST .../retry` or `{keep_plan, plan_digest, waive, reinstate}` | fresh or keep-plan retry |
| `GET /clean-preview`, `POST /clean {token}` | the token hashes the preview; a changed preview is 409 |
| `GET /tasks/{id}/questions`, `POST .../answer {round, answers}` | the planner's open questions and your answers (at most 20,000 bytes). A stale `round` or a task not in `open_questions` is 409. `POST /tasks` also takes `grill` (default: the `grill` setting) |
| `POST /tasks/{id}/advance`, `/abandon`, `/recover`, `/rebase`, `POST /eval-audit {runs, max_usd, only, stage_config}` | jobs |
| `GET /jobs`, `GET /jobs/{id}` | job state: `queued`, `running`, `succeeded`, `failed`, `interrupted` |

Errors are `{error, detail}`: 401 token, 403 host or origin, 404 unknown task or job, 409 wrong state or stale preview, 422 bad input.
