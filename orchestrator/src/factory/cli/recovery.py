from types import SimpleNamespace

from factory.audit_gaps import split_quoting_waived
from factory.cli.base import _ask_with_waiver, _coded, _ref, _run_job, _task_path
from factory.client import ServerRefused
from factory.store import task_from_dict
from factory.task import TaskState
from factory.textsafe import escape_control
from factory.waiver import note_lines


def _retry(args, io, ctx) -> int:
    ctx.server.get(_task_path(args.task_id))
    if args.keep_plan:
        return _retry_keeping_plan(args.task_id, io, ctx)
    try:
        new = ctx.server.post(_task_path(args.task_id, "/retry"))
    except ServerRefused as refused:
        if refused.error != "retry refused":
            raise
        print(f"retry refused: {refused.detail}", file=io.stderr)
        return 1
    print(new["id"], file=io.stdout)
    return 0


def _retry_keeping_plan(task_id: str, io, ctx) -> int:
    if not io.stdin.isatty():
        print("keeping a plan needs an interactive terminal", file=io.stderr)
        return 1
    try:
        task = ctx.server.get(_task_path(task_id, "/retry-preview"))
    except ServerRefused as refused:
        if refused.error != "retry refused":
            raise
        print(f"retry refused: {refused.detail}", file=io.stderr)
        return 1
    waived_earlier = tuple(task["waived_findings"])
    note_display, required = note_lines(tuple(task["review_findings"]))
    lines = [
        f"RETRY of {task_id} ({task['state']}), keeping its approved plan", f"GOAL:\n{task['goal']}", f"PLAN:\n{task['plan']}", "RISKS:",
        *(f"  - {r}" for r in task["risks"]), "REVIEWER NOTES (sent to the implementer):", *note_display,
    ]
    if waived_earlier:
        lines += ["WAIVED EARLIER (not sent):", *(f"  - {f}" for f in waived_earlier)]
        lines += ["REINSTATE with r N:", *(f"  - [r{i}] {f}" for i, f in enumerate(waived_earlier, 1))]
    gaps = tuple(task["audit_gaps"])
    if gaps:
        lines += ["AUDIT FINDINGS (sent to the next attempt):", *(f"  - {g}" for g in gaps)]
    if task["failure_reason"]:
        lines.append(f"FAILED BECAUSE: {_coded('failure_reason', task['failure_reason'])}")
    if task["state"] == TaskState.AUDITED.value:
        lines += ["AUDIT FINDINGS:", *(f"  - {f}" for f in task["audit_findings"]),
                  f"{task_id} is audited and still mergeable; abandon it if you take this retry"]
    print(escape_control("\n".join(lines)), file=io.stdout)
    if task["warning"]:
        print(task["warning"], file=io.stdout)
    answered = _ask_with_waiver(
        io, f"Queue this plan again as a new task (retry of {task_id})? [y/N]", "Waive these and queue? [y/N] ", required, "not queued",
        waived_earlier)
    if answered is None:
        return 1
    waived, reinstated = answered
    for dropped in split_quoting_waived(gaps, waived)[1]:
        print(escape_control(f"dropped (quotes a waived note): {dropped}"), file=io.stdout)
    try:
        new = ctx.server.post(_task_path(task_id, "/retry"), {
            "keep_plan": True, "plan_digest": task["plan_digest"], "waive": list(waived), "reinstate": list(reinstated)})
    except ServerRefused as refused:
        if refused.error == "invalid waiver":
            action = "reinstate" if reinstated else "waive"
            print(f"cannot {action}: {escape_control(refused.detail)}; nothing queued", file=io.stderr)
        elif refused.error == "retry refused":
            print("plan changed since display; nothing queued", file=io.stderr)
        else:
            raise
        return 1
    print(f"{_ref(ctx, new['id'])}: queued with the approved plan; run: factory advance {_ref(ctx, new['id'])}", file=io.stdout)
    return 0


def _recover(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    if _run_job(ctx, io, _task_path(args.task_id, "/recover")) is None:
        return 1
    print(f"{task.id}: marked failed (interrupted); worktree {task.worktree}", file=io.stdout)
    print("Agents the dead driver recorded were killed first; one launched in the instant before SIGKILL may be unrecorded: check before cleaning.", file=io.stdout)
    print("Its work is uncommitted in the worktree; `factory clean` would discard it.", file=io.stdout)
    return 0


def _abandon(args, io, ctx) -> int:
    if _run_job(ctx, io, _task_path(args.task_id, "/abandon")) is None:
        return 1
    print(f"{args.task_id}: abandoned; `factory clean` removes its worktree (a killed driver may have left an agent running)", file=io.stdout)
    return 0


def _rebase(args, io, ctx) -> int:
    rebased = _run_job(ctx, io, _task_path(args.task_id, "/rebase"))
    if rebased is None:
        return 1
    print(f"{_ref(ctx, args.task_id)}: rebased onto main, back to agent_done; run `factory advance {_ref(ctx, args.task_id)}` to re-verify and re-audit", file=io.stdout)
    print(f"the previously audited commit was {rebased['old_audited_sha']}; it is unreachable once the branch is cleaned", file=io.stdout)
    return 0


def _merged_branch_flag(candidate) -> str:
    """`clean` tries `branch -d`: it needs main on a branch, and git must agree the branch is merged."""
    if candidate.commits_ahead:
        return "branch will be deleted if git agrees it is merged"
    return "branch will be deleted if main is on a branch"


def _describe_candidate(candidate) -> str:
    flags = ["worktree present" if candidate.directory_exists else "worktree missing"]
    flags += ["dirty (uncommitted work will be discarded)"] if candidate.dirty else []
    flags += [f"{candidate.commits_ahead} commit{'s' if candidate.commits_ahead != 1 else ''} ahead of base"] if candidate.commits_ahead else []
    will_delete_branch = candidate.branch_empty and not candidate.outside_root and candidate.task.state is TaskState.FAILED  # merged ones use `-d`
    flags += ["empty branch will be deleted"] if will_delete_branch else []
    if candidate.branch_exists and not will_delete_branch:
        flags += [_merged_branch_flag(candidate) if candidate.task.state is TaskState.MERGED else "branch left behind"]
    flags += ["worktree path is not <root>/<id>: will be skipped"] if candidate.outside_root else []
    flags += [f"gate cache {candidate.cache_bytes} bytes"] if candidate.cache_bytes is not None else []
    return f"{candidate.task.id}  {candidate.task.state.value}: " + ", ".join(flags)


def _candidate_from_view(view: dict) -> SimpleNamespace:
    fields = {name: value for name, value in view.items() if name not in ("id", "state")}
    return SimpleNamespace(task=SimpleNamespace(id=view["id"], state=TaskState(view["state"])), **fields)


def _clean(args, io, ctx) -> int:
    if not io.stdin.isatty():
        print("clean needs an interactive terminal", file=io.stderr)
        return 1
    preview = ctx.server.get("/clean-preview")
    candidates = [_candidate_from_view(c) for c in preview["candidates"]]
    orphans, skipped = preview["orphans"], preview["skipped"]
    if not (candidates or orphans or skipped):
        print("nothing to clean", file=io.stdout)
        return 0
    lines = [_describe_candidate(c) for c in candidates]
    lines += [f"orphan gate cache {o['name']}: {o['size']} bytes, no task record" for o in orphans]
    lines += [f"skipped cache entry {name}: not a plain directory with a readable task record" for name in skipped]
    print(escape_control("\n".join(lines)), file=io.stdout)
    if not (candidates or orphans):
        return 1
    print("Clean these? [y/N] ", end="", file=io.stdout)
    io.stdout.flush()
    if io.stdin.readline().strip() != "y":
        print("not cleaned", file=io.stdout)
        return 1
    try:
        result = _run_job(ctx, io, "/clean", {"token": preview["token"]})
    except ServerRefused as refused:
        if refused.error != "clean refused":
            raise
        print(f"clean refused: {refused.detail}", file=io.stderr)
        return 1
    if result is None:
        return 1
    for outcome in result["outcomes"]:
        for line in outcome["done"] + outcome["problems"]:
            print(escape_control(f"{outcome['task_id']}: {line}"), file=io.stdout)
    return 1 if skipped or any(o["problems"] for o in result["outcomes"]) else 0


TOOL_CONFIG_HEADING = "ROOT TOOL CONFIG (tools on your machine may read or run these after the merge; review them):"


def _tool_config_lines(paths: list[str]) -> list[str]:
    return [TOOL_CONFIG_HEADING] + [f"  - {path}" for path in paths] if paths else []


def _merge(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    if not io.stdin.isatty():
        print("merge needs an interactive terminal", file=io.stderr)
        return 1
    if task.state is not TaskState.AUDITED:
        print(f"{task.id} is {task.state.value}, not audited", file=io.stderr)
        return 1
    try:
        preview = ctx.server.get(_task_path(args.task_id, "/merge-preview"))
    except ServerRefused as refused:
        if refused.error != "merge refused":
            raise
        print(escape_control(f"merge refused: {refused.detail}"), file=io.stderr)
        return 1
    shown = "\n".join([f"MERGE {task.id}: fast-forward main to {preview['audited_sha']}",
                       f"branch: {preview['branch']}", "CHANGES:", preview["diffstat"]]
                      + _tool_config_lines(preview["tool_config"])
                      + ["AUDIT FINDINGS:"] + [f"  - {f}" for f in preview["findings"]])
    print(escape_control(shown), file=io.stdout)
    print("Merge? [y/N] ", end="", file=io.stdout)
    io.stdout.flush()
    if io.stdin.readline().strip() != "y":
        print("not merged", file=io.stdout)
        return 1
    try:
        merged = _run_job(ctx, io, _task_path(args.task_id, "/merge"), {"audited_sha": preview["audited_sha"]})
    except ServerRefused as refused:
        if refused.error != "merge refused":
            raise
        print(escape_control(f"merge refused: {refused.detail}"), file=io.stderr)
        return 1
    if merged is None:
        return 1
    print(f"{task.id}: merged; commits are unsigned: sign them before pushing", file=io.stdout)
    return 0
