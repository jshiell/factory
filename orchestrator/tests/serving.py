from contextlib import contextmanager

from starlette.testclient import TestClient

from factory.repos import RepoRegistry
from factory.server import create_secured_repos_app, load_or_create_token


def repos_app(ctx, token, name="proj"):
    """The guarded multi-repo app with `ctx` as its one repo, `name` (proj by default)."""
    return create_secured_repos_app(RepoRegistry({name: ctx}, {}), token, ctx.settings.server_allowed_hosts, ctx.clock)


@contextmanager
def served(ctx):
    """A client of the real, guarded app: loopback host and the bearer token on every request."""
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(repos_app(ctx, token), base_url="http://127.0.0.1:8765",
                    headers={"Authorization": f"Bearer {token}"}) as client:  # entering runs the lifespan: the job worker starts
        yield client
