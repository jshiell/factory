from factory.cli.base import IO, _print_reaped
from factory.clone_repo import RepositoryError, clone_repository
from factory.github import GitHubError
from factory.ledger import Ledger
from factory.lock import AlreadyRunning, state_lock
from factory.migrate import DatabaseNotEmpty, migrate
from factory.process_registry import FileRecordsBlocked, clear_file_records
from factory.store import TaskStore
from factory.textsafe import escape_control


def _migrate(args, io, ctx) -> int:
    try:
        with state_lock(ctx.settings.state_dir):
            cleared = clear_file_records(ctx.settings.state_dir, ctx.probe, ctx.group_alive)
            for path in cleared.removed:
                print(f"removed stale process record {path.name}", file=io.stdout)
            for invalid in cleared.discarded:
                print(f"removed invalid process record {invalid.path.name}", file=io.stdout)
            return _copy_old_files(io, ctx, cleared_any=bool(cleared.removed or cleared.discarded))
    except FileRecordsBlocked as blocked:
        print(f"old process records remain: {blocked}; nothing changed", file=io.stderr)
        return 1
    except AlreadyRunning:
        print("another factory run holds the lock; nothing changed", file=io.stderr)
        return 1


def _copy_old_files(io, ctx, cleared_any: bool) -> int:
    try:
        report = migrate(TaskStore(ctx.settings.tasks_dir), Ledger(ctx.settings.ledger_path), ctx.store, ctx.ledger)
    except DatabaseNotEmpty as refused:
        if cleared_any:  # the process records were the work that remained; the task copy was done earlier
            print(f"{refused}; task files not copied", file=io.stdout)
            return 0
        print(f"{refused}; nothing changed", file=io.stderr)
        return 1
    print(f"migrated {report.tasks} tasks, {report.ledger_entries} ledger entries", file=io.stdout)
    for name in report.skipped:
        print(f"skipped: {name}", file=io.stdout)
    return 0


def _serve(args, io, ctx) -> int:
    from factory.server import (
        StartRefused,
        TokenFileUnsafe,
        claim_state_dir,
        create_secured_app,
        load_or_create_token,
    )
    port = args.port if args.port is not None else ctx.settings.server_port
    try:
        token = load_or_create_token(ctx.settings.state_dir)
        with claim_state_dir(ctx) as reaped:
            _print_reaped(reaped.killed, reaped.discarded, io)
            print(f"serving on http://127.0.0.1:{port}; token in {ctx.settings.state_dir / 'token'}", file=io.stdout)
            io.stdout.flush()
            ctx.serve(create_secured_app(ctx, token), "127.0.0.1", port)
    except (StartRefused, TokenFileUnsafe) as refused:
        print(escape_control(str(refused)), file=io.stderr)
        return 1
    return 0


def _watch(args, io, ctx) -> int:
    try:
        with state_lock(ctx.settings.state_dir, name="watch"):
            return _watch_locked(args, io, ctx)
    except AlreadyRunning:
        print("another factory watch holds the lock; nothing changed", file=io.stderr)
        return 1


def _watch_locked(args, io, ctx) -> int:
    if not ctx.settings.watch_approvers:
        print("watch needs watch_approvers: set it in the repo's factory.toml to the GitHub logins whose issues may start tasks", file=io.stderr)
        return 1
    try:
        owner, name = ctx.origin_slug(ctx.settings.repo)
    except RepositoryError as error:
        print(f"watch needs a GitHub origin: {error}", file=io.stderr)
        return 1
    try:
        github = ctx.github(owner, name)
    except ValueError as error:
        print(f"watch needs a plain GitHub origin: {escape_control(str(error))}", file=io.stderr)
        return 1
    try:
        github.self_user()
    except FileNotFoundError:
        print("watch needs the gh CLI: gh is not installed", file=io.stderr)
        return 1
    except GitHubError:
        print("watch needs a logged-in gh: run `gh auth login`", file=io.stderr)
        return 1
    try:
        is_private = github.repo_is_private()
    except GitHubError as error:
        print(f"watch cannot read the repository from GitHub: {escape_control(error.stderr.strip())}", file=io.stderr)
        return 1
    if not is_private and not args.allow_public:
        print("this repo is public: plans, failure reasons and worktree paths would become public comments; pass --allow-public to watch it anyway", file=io.stderr)
        return 1
    approver_ids = set()
    for login in ctx.settings.watch_approvers:
        try:
            approver_ids.add(github.user_id(login))
        except GitHubError:
            print(f"watch cannot resolve the approver {login!r} on GitHub: check watch_approvers", file=io.stderr)
            return 1
    try:
        while True:
            ctx.watch_pass(github, ctx.server, ctx.settings.state_dir, approver_ids, merge=ctx.settings.watch_merge, retry=ctx.settings.watch_retry)
            if args.once:
                return 0
            ctx.sleep(args.interval)
    except KeyboardInterrupt:
        print("watch stopped", file=io.stderr)
        return 0


def _clone(spec: str, io: IO) -> int:
    try:
        return clone_repository(spec, io)
    except RepositoryError as error:
        print(error, file=io.stderr)
        return 1
