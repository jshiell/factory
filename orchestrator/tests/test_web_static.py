import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import factory.web
from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore

static_dir = Path(factory.web.__file__).parent / "static"


@pytest.fixture
def client(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    ctx = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                  clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    with TestClient(create_secured_app(ctx, load_or_create_token(settings.state_dir)), base_url="http://127.0.0.1:8765") as client:
        yield client


def recorded_sha256(name: str) -> str:
    row = next(line for line in (static_dir / "VENDORED.md").read_text().splitlines() if f"`{name}`" in line)
    return re.search(r"`([0-9a-f]{64})`", row).group(1)


@pytest.mark.parametrize("name", ["htmx.min.js", "sse.min.js", "pico.classless.min.css"])
def test_a_vendored_file_is_served_without_a_session_and_matches_its_recorded_hash(client, name):
    response = client.get(f"/ui/static/{name}")

    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == recorded_sha256(name)
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["cache-control"] == "no-store"


def test_a_static_file_still_refuses_a_foreign_origin_and_site(client):
    assert client.get("/ui/static/htmx.min.js", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/ui/static/htmx.min.js", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/ui/static/htmx.min.js", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("path", ["/ui/static/../routes.py", "/ui/static/%2e%2e/routes.py", "/ui/static/..%2froutes.py",
                                  "/ui/static/%2e%2e%2froutes.py"])
def test_guard_a_traversal_path_does_not_leave_the_static_directory(client, path):
    response = client.get(path)

    assert "ui_routes" not in response.text
    assert response.status_code in {401, 403, 404}
