from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

from factory.cli.base import _ask_with_waiver, _coded, _run_job, _task_path
from factory.client import ServerRefused
from factory.ids import require_safe_id
from factory.question_text import questions_text
from factory.store import task_from_dict
from factory.task import Task, TaskState
from factory.textsafe import escape_control
from factory.waiver import note_lines


def _new(args, io, ctx) -> int:
    body = {"goal": args.goal, **({"grill": False} if args.no_grill else {})}
    print(ctx.server.post("/tasks", body)["id"], file=io.stdout)
    return 0


def _ui(args, io, ctx) -> int:
    from factory.server import _LOOPBACK_HOSTS
    if args.host is not None and args.host not in _LOOPBACK_HOSTS | set(ctx.settings.server_allowed_hosts):
        print(f"usage error: --host {args.host} is not loopback or in server_allowed_hosts", file=io.stderr)
        return 2
    url = ctx.server.post("/ui-login-codes")["url"]
    print(url if args.host is None else _with_host(url, args.host), file=io.stdout)
    return 0


def _with_host(url: str, host: str) -> str:
    parts = urlsplit(url)
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


def _list(args, io, ctx) -> int:
    listing = ctx.server.get("/tasks")
    for task in listing["tasks"]:
        reason = f"  {_coded('failure_reason', task['failure_reason'])}" if task["failure_reason"] else ""
        print(escape_control(f"{task['id']}  {task['state']}{reason}"), file=io.stdout)
    for name in listing["skipped"]:
        print(escape_control(f"skipped: {name}"), file=io.stdout)
    return 0


def _describe(task: Task) -> list[str]:
    lines = [f"id: {task.id}", f"state: {task.state.value}", f"goal: {task.goal}"]
    if task.failure_reason:
        lines.append(f"failure: {_coded('failure_reason', task.failure_reason)}")
    if task.retry_of:
        lines.append(f"retry_of: {task.retry_of}")
    if task.fix_rounds_used:
        lines.append(f"fix_rounds_used: {task.fix_rounds_used}")
    if task.open_questions:
        lines.append(f"questions (round {task.grill_rounds}):")
        lines += [f"  {q['number']}. {q['title']} (recommended: {q['recommended']})" for q in task.open_questions]
    lines += [f"plan:\n{task.plan}"] if task.plan else []
    for label, items in (("risks", task.plan_risks), ("review findings", task.review_findings),
                         ("audit findings", task.audit_findings)):
        lines += [f"{label}:"] + [f"  - {item}" for item in items] if items else []
    for label, value in (("plan_error", task.plan_error), ("review_error", task.review_error),
                         ("audit_error", task.audit_error)):
        if value:
            lines.append(f"{label}: {_coded(label, value)}")
    if task.worktree:
        lines += [f"worktree: {task.worktree}", f"branch: factory/{task.id}"]
    return lines


def _show(args, io, ctx) -> int:
    task = task_from_dict(ctx.server.get(_task_path(args.task_id)))
    print(escape_control("\n".join(_describe(task))), file=io.stdout)
    return 0


def _advance(args, io, ctx) -> int:
    result = _run_job(ctx, io, _task_path(args.task_id, "/advance"))
    if result is None:
        return 1
    for step in result["steps"]:
        print(f"{step['stage']} -> {step['state']}", file=io.stdout)
    reason = result["failure_reason"]
    print(f"state: {result['state']}" + (f" ({_coded('failure_reason', reason)})" if reason else ""), file=io.stdout)
    if result["stopped"]:
        print(f"stopped: {result['stopped']}", file=io.stdout)
        if stopped_error := result.get("stopped_error"):
            field, code = stopped_error["field"], stopped_error["code"]
            print(f"{field}: {_coded(field, code)}", file=io.stdout)
        return 1
    if result["state"] == TaskState.REVIEWED.value:
        print(f"review passed; run: factory approve {args.task_id}", file=io.stdout)
    if result["state"] == TaskState.OPEN_QUESTIONS.value:
        print(f"open questions; run: factory answer {args.task_id}", file=io.stdout)
    return 1 if result["state"] == TaskState.FAILED.value else 0


def _answer(args, io, ctx) -> int:
    ctx.server.get(_task_path(args.task_id))
    if not io.stdin.isatty():
        print("answering needs an interactive terminal", file=io.stderr)
        return 1
    try:
        shown = ctx.server.get(_task_path(args.task_id, "/questions"))
    except ServerRefused as refused:
        if refused.error != "wrong state":
            raise
        print(refused.detail, file=io.stderr)
        return 1
    print(escape_control(_questions_display(shown)), file=io.stdout)
    lines = []
    for question in shown["questions"]:
        print(escape_control(f"{question['number']}. answer [Enter = {question['recommended']}]: "), file=io.stdout, end="")
        typed = io.stdin.readline()
        if not typed:
            print("no answer given; nothing answered", file=io.stderr)
            return 1
        lines.append(f"{question['number']}. {typed.strip() or question['recommended']}")
    try:
        ctx.server.post(_task_path(args.task_id, "/answer"), {"round": shown["round"], "answers": "\n".join(lines)})
    except ServerRefused as refused:
        if refused.error == "round changed":
            print("questions changed since display; nothing answered", file=io.stderr)
        elif refused.error == "wrong state":
            print(refused.detail, file=io.stderr)
        else:
            raise
        return 1
    print(f"{args.task_id}: answered; run: factory advance {args.task_id}", file=io.stdout)
    return 0


def _questions_display(shown: dict) -> str:
    return f"ROUND {shown['round']}\n\n{questions_text(shown['questions'])}"


def _not_reviewed(task: Task, io) -> int:
    print(f"{task.id} is {task.state.value}, not reviewed", file=io.stderr)
    return 1


def _reject(args, io, ctx) -> int:
    try:
        task = ctx.server.post(_task_path(args.task_id, "/reject"))
    except ServerRefused as refused:
        if refused.error != "wrong state":
            raise
        print(refused.detail, file=io.stderr)
        return 1
    print(f"{task['id']}: plan rejected", file=io.stdout)
    return 0


def _approve(args, io, ctx) -> int:
    ctx.server.get(_task_path(args.task_id))
    if not io.stdin.isatty():
        print("approval needs an interactive terminal", file=io.stderr)
        return 1
    try:
        preview = ctx.server.get(_task_path(args.task_id, "/approval"))
    except ServerRefused as refused:
        if refused.error != "wrong state":
            raise
        print(refused.detail, file=io.stderr)
        return 1
    note_display, required = note_lines(tuple(preview["review_findings"]))
    shown = "\n".join(
        [f"GOAL:\n{preview['goal']}", f"PLAN:\n{preview['plan']}", "RISKS:"] + [f"  - {r}" for r in preview["risks"]]
        + ["REVIEWER NOTES (sent to the implementer):"] + note_display
    )
    print(escape_control(shown), file=io.stdout)
    if preview["warning"]:
        print(preview["warning"], file=io.stdout)
    answered = _ask_with_waiver(io, "Approve this plan? [y/N]", "Waive these and approve? [y/N] ", required, "not approved")
    if answered is None:
        return 1
    waived, _ = answered
    try:
        ctx.server.post(_task_path(args.task_id, "/approve"), {"plan_digest": preview["plan_digest"], "waive": list(waived)})
    except ServerRefused as refused:
        if refused.error == "invalid waiver":
            print(f"cannot waive: {escape_control(refused.detail)}; nothing approved", file=io.stderr)
        elif refused.error == "plan changed":
            print("plan changed since display; nothing approved", file=io.stderr)
        elif refused.error == "wrong state":
            print(refused.detail, file=io.stderr)
        else:
            raise
        return 1
    waived_note = f" ({len(waived)} required note{'s' if len(waived) != 1 else ''} waived)" if waived else ""
    print(f"{args.task_id}: approved and queued{waived_note}; run: factory advance {args.task_id}", file=io.stdout)
    return 0


def _costs(args, io, ctx) -> int:
    report = SimpleNamespace(**ctx.server.get("/costs", **({"task_id": require_safe_id(args.task_id)} if args.task_id else {})))
    print("by stage:", file=io.stdout)
    for stage, cost in sorted(report.by_stage.items()):
        print(f"  {stage}  ${cost:.4f}", file=io.stdout)
    print("by task:", file=io.stdout)
    for task_id, cost in sorted(report.by_task.items()):
        print(f"  {escape_control(task_id)}  ${cost:.4f}", file=io.stdout)
    print(f"assumed: ${report.assumed:.4f}", file=io.stdout)
    print(f"total: ${report.total:.4f}", file=io.stdout)
    return 0
