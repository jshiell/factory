from contextlib import contextmanager

from starlette.testclient import TestClient

from factory.server import create_secured_app, load_or_create_token


@contextmanager
def served(ctx):
    """A client of the real, guarded app: loopback host and the bearer token on every request."""
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765",
                    headers={"Authorization": f"Bearer {token}"}) as client:  # entering runs the lifespan: the job worker starts
        yield client
