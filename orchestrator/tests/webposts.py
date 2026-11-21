"""Helpers for tests that send state-changing requests (POST and other non-GET methods) to /ui paths."""

UI_ORIGIN = "http://127.0.0.1:8765"


def same_origin_headers() -> dict[str, str]:
    """The `Origin` header a write under /ui needs: exactly the test client's own origin."""
    return {"Origin": UI_ORIGIN}
