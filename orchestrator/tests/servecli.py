from factory.cli import ServerContext
from factory.process_registry import ReapResult
from factory.settings import GlobalSettings
from test_server_repos import make_ctx


def make_server(tmp_path, names=("a",), skipped=None, **settings) -> ServerContext:
    """A `serve` context over repos under `<tmp>/home` that records what it would serve instead of listening."""
    home = tmp_path / "home"
    contexts = {name: make_ctx(home, name) for name in names}
    for context in contexts.values():
        context.reap = lambda records: ReapResult([], [])
    server = ServerContext(GlobalSettings(home=home, **settings), load_repos=lambda: (dict(contexts), dict(skipped or {})))
    server.contexts = contexts
    server.served = []
    server.serve = lambda app, host, port: server.served.append((app, host, port))
    return server
