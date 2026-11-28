from urllib.parse import urlsplit, urlunsplit

from factory.cli.base import IO, _print_reaped
from factory.clone_repo import RepositoryError, clone_repository
from factory.textsafe import escape_control

_PLAIN_HTTP = ("the connection is plain HTTP, so the token, login code and session cookie can be read on the way: "
               "use a VPN or SSH tunnel")


def _bind_warning(host: str, port: int, allowed_hosts) -> str | None:
    """One stderr line for a start that is reachable beyond loopback, or None; never two, so none contradicts another."""
    names = ", ".join(allowed_hosts)  # validated in settings to [a-z0-9.-], so safe to print
    if allowed_hosts and host == "127.0.0.1":
        return (f"warning: bound to 127.0.0.1 port {port}, accepting only loopback and the Host names {names} "
                f"(for a tunnel that keeps the Host); {_PLAIN_HTTP}")
    if allowed_hosts:
        return (f"warning: bound to {host}, so port {port} is reachable from the network and accepts the Host names "
                f"{names} besides loopback; {_PLAIN_HTTP}")
    if host != "127.0.0.1":
        return (f"warning: bound to {host}, so port {port} is reachable from the network; "
                "requests whose Host is not loopback are still refused (403)")
    return None


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


def _serve(args, io, ctx) -> int:
    from factory.server import (
        StartRefused,
        TokenFileUnsafe,
        claim_server,
        create_secured_repos_app,
        load_or_create_token,
    )
    settings = ctx.settings
    port = args.port if args.port is not None else settings.server_port
    host = args.host or settings.server_host
    try:
        token = load_or_create_token(settings.server_dir)
        contexts, skipped = ctx.load_repos()
        with claim_server(settings.server_dir, contexts, skipped) as claimed:
            for name, reaped in claimed.reaped.items():
                _print_reaped(reaped.killed, reaped.discarded, io, repo=name)
            for name, why in sorted(claimed.registry.skipped.items()):
                print(escape_control(f"skipped repo {name}: {why}"), file=io.stderr)
            print(f"serving on http://127.0.0.1:{port}; token in {settings.token_path}", file=io.stdout)
            io.stdout.flush()
            for name in claimed.registry.started_names:
                old_token = claimed.registry.get(name).settings.state_dir / "token"
                if old_token.exists():
                    print(f"warning: {old_token} is unused; the token is now in {settings.token_path}", file=io.stderr)
            warning = _bind_warning(host, port, settings.server_allowed_hosts)
            if warning:
                print(warning, file=io.stderr)
            ctx.serve(create_secured_repos_app(claimed.registry, token, settings.server_allowed_hosts, ctx.clock), host, port)
    except (StartRefused, TokenFileUnsafe) as refused:
        print(escape_control(str(refused)), file=io.stderr)
        return 1
    return 0


def _clone(spec: str, io: IO) -> int:
    try:
        return clone_repository(spec, io)
    except RepositoryError as error:
        print(error, file=io.stderr)
        return 1
