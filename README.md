# factory

A personal "agentic software factory": a small Python orchestrator that takes a one-line goal and drives headless
[Claude Code](https://claude.com/claude-code) agents (`claude -p`) through **plan → review → implement → verify → audit → merge**,
with a human approving the plan and the merge.

It is a learning project, built plan by plan (`docs/plan-1.md` … `docs/plan-18.md`), test-first. The point is to find out how far you
can trust unattended agents when every step is sandboxed, every result is checked by something that is not the agent, and the
only things that need a person are the two decisions that matter: *is this the right plan* and *should this land on main*.

## Layout

| Path | What it is |
|---|---|
| `orchestrator/` | The factory: a Python 3.14 package, CLI `factory`, standard library only, ~730 tests |
| `target/` | A tiny Python project (`calc`) the agents work on. Replace it with any `uv` + `pytest` project |
| `docs/` | One `plan-N.md` per increment (decisions, cycles, reviewer findings) and `spike-findings.md` (what was measured, known gaps) |
| `spike/` | Throwaway probes and end-to-end scripts. Not production code; several make real API calls or need a terminal |

## Requirements

- macOS (the sandbox is Seatbelt via `nono`; written against nono 0.81)
- `git`, [`uv`](https://docs.astral.sh/uv/), Python 3.14
- the `claude` CLI, logged in (OAuth). `ANTHROPIC_API_KEY` is stripped from the agents' environment on purpose
- `nono` on `PATH`

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
| `new <goal>` | Create a task (`proposed`) |
| `list` / `show <id>` | List tasks, or show one task's state, goal, plan, risks, review and audit findings, failure reason, worktree and branch |
| `advance <id>` | Run the next automatic stages until a human gate, a failure or the step cap |
| `approve <id>` / `reject <id>` | Accept or reject a reviewed plan. `approve` needs a TTY and binds to a digest of the plan you saw |
| `merge <id>` | Fast-forward `main` to the audited commit. Needs a TTY; shows what will land first |
| `retry <id>` | Start a fresh task from a failed or audited one (new id, `retry_of` set) |
| `rebase <id>` | An audited task whose merge was refused because `main` moved: replay it on the new `main` and send it back through verify and audit |
| `abandon <id>` | Fail a task that is not running (for example one stuck mid-rebase) |
| `recover <id>` | Mark a task whose driver died (`running`) as failed, charging an assumed cost |
| `clean` | After a preview and confirmation, remove worktrees, gate caches, orphan caches and branches of finished tasks |
| `costs [id]` | Spend by stage and by task, with assumed costs shown separately |

Exit codes: `0` ok, `1` refused or failed (the reason is printed), `2` usage error.

### Configuration

Optional `factory.toml` in the working directory (or `--config <file>`). Relative paths resolve against the file. Unknown keys are an error.

```toml
repo = "."                     # default: the git toplevel
state_dir = ".factory"         # tasks, ledger, gate caches, lock
worktree_root = ".worktrees"   # one git worktree per task
project_subdir = "target"      # where the agent works and the tests run
test_argv = ["uv", "run", "pytest", "-q"]
model = "sonnet"
budget_usd = 0.5               # per implementer call
cap_usd = 5.0                  # total spend cap for a task
max_turns = 30
fix_rounds = 2                 # how many times the implementer may be asked to fix a failing gate
gate_timeout_s = 120
runner_timeout_s = 300
claude_executable = "claude"
sandbox_executable = "nono"
allowed_tools = []
```

## Design

### Task lifecycle

Every task is a JSON file in `.factory/tasks/`; all changes go through one state machine (`task.py`), so an
illegal move is an error, not a bug waiting to happen.

```mermaid
stateDiagram-v2
    [*] --> proposed: new
    proposed --> planned: plan agent (read-only)
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
    merge -. "main moved" .-> rebase["rebase<br/>human-run"]
    rebase --> gate
```

### Components

```mermaid
flowchart TB
    cli["cli.py<br/>commands, TTY prompts, exit codes"]
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
    store["store.py<br/>one JSON file per task, atomic writes"]
    runner["runner.py / command.py<br/>build and run `claude -p` under nono"]
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

- **Sandboxed.** Every agent runs under `nono` (Seatbelt). It can read and write its own worktree and nothing else: not the main
  checkout, `.git/config`, the home directory, or other tasks. The implementer has no `Bash` and no `git`; plan, review and audit
  agents have no write tools at all.
- **The agent never commits and never judges its own work.** The factory commits (unsigned, factory identity, hooks off) and
  then runs the gate itself.
- **The gate is a clean checkout.** Before the tests run, the worktree is committed and cleaned (`git clean -ffdx`), so only
  committed files can influence the result. Git itself is run with `fsmonitor`, hooks and submodule tricks disabled, and a nested
  `.git` anywhere under the project is refused.
- **Tamper checks.** Existing tests may be added to but not removed, shrunk, renamed or shadowed (a duplicate `def test_x` that
  disables the first is caught). Protected config (`conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, the pytest table of any
  `pyproject.toml` from the repo root down to the project) may not change. Every change must be inside the project subdir:
  anything outside it (root config, `uv.toml`, `sitecustomize.py`, a nested repo) fails the gate as `outside_project`.
- **Tests run sandboxed too**, with a private per-task `uv` cache and a filtered environment. A failing gate triggers up to
  `fix_rounds` fix rounds; a frozen reference prevents the fixer from rewriting tests it was already judged against.
- **Audit.** A separate read-only agent reviews the diff against the plan and returns a structured verdict. It is a second opinion,
  not a proof: its reliability is unmeasured.
- **Humans hold the two irreversible steps.** `approve` binds to a digest of the plan shown; `merge` shows the exact diff and only
  fast-forwards to the exact audited commit. Both refuse without a TTY, so an agent or a script cannot approve itself.
- **Moved main.** `merge` never merges; if `main` moved it refuses. `rebase` replays the audited commits and returns the task to
  `agent_done`, so it must pass the gate and a fresh audit again before `merge` will accept it. `rebase` grants no authority,
  so it needs no TTY, but it is never called by `advance`.
- **Money.** Every call is metered into `ledger.jsonl`; a per-task cap stops further calls. Interrupted calls are charged an
  assumed cost so a crash cannot hide spend.
- **One driver at a time.** A lock in the state directory stops two factory processes mutating tasks concurrently; killing the
  driver kills the agent tree.

### Why this shape

- *Plain files and a state machine* so everything is inspectable and a crashed run is recoverable by hand.
- *Standard library only* in the orchestrator, to keep the trusted core small.
- *One git worktree and branch (`factory/<id>`) per task* so agents never touch each other or the main checkout, and a rejected
  task is just a directory to delete (`clean`).
- *Every stage is a function of the task file* (`task_id → Task`), which makes each stage testable with fakes and `advance` a loop.

## Development

```sh
cd orchestrator && uv run pytest -q      # ~730 tests, real git in temp repos
cd target && uv run pytest -q
```

Work follows the plan-per-increment flow in `docs/`: spike → `plan-N.md` → independent plan review → red/green/commit per behaviour.
Anything that needs a terminal, nono, the network or the home directory is covered by a throwaway script in `spike/` that you run by hand.

## Known limits

Recorded per plan in `docs/spike-findings.md`. The main ones:

- A new test file can monkeypatch the code under test at import time; the gate cannot tell.
- Files *inside* the project that influence a run (`uv.toml`, `.python-version`, `sitecustomize.py`, `.env` under `target/`) are not watched.
  Legitimate edits outside the project (a root README, root config) are refused and need a human.
- A rebase can change behaviour without a textual conflict; only the tests and the fresh audit catch that.
- A SIGKILLed driver can leave an orphan agent or a half-done rebase; `abandon`/`recover` and a manual check are the way out.
- Submodules are only partly handled, and the auditor is a model, not a guarantee.
- macOS only.
