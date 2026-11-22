import secrets
import time
from datetime import timedelta

CODE_LIFETIME = timedelta(minutes=5)
SESSION_IDLE_LIMIT = timedelta(hours=8)


def session_cookie_name(host: str) -> str:
    """Named for the Host's port, so servers on different loopback ports do not overwrite each other's cookie."""
    _, _, port = host.rpartition(":")
    return f"factory_session_{port if port.isdigit() else 80}"


class LoginCodes:
    """Single-use login codes that expire; held in memory."""

    def __init__(self, clock):
        self._clock = clock
        self._expiries = {}

    def issue(self) -> str:
        now = self._clock()
        self._expiries = {code: expiry for code, expiry in self._expiries.items() if now < expiry}
        code = secrets.token_urlsafe(32)
        self._expiries[code] = now + CODE_LIFETIME
        return code

    def redeem(self, code: str) -> bool:
        expiry = self._expiries.pop(code, None)
        return expiry is not None and self._clock() < expiry


class Sessions:
    """Browser sessions, held in memory so a restart logs everyone out. One unused for `SESSION_IDLE_LIMIT` expires.

    `clock` returns seconds on a monotonic scale.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last_used = {}

    def create(self) -> str:
        self._drop_expired()
        session_id = secrets.token_urlsafe(32)
        self._last_used[session_id] = self._clock()
        return session_id

    def valid(self, session_id: str) -> bool:
        self._drop_expired()
        if session_id not in self._last_used:
            return False
        self._last_used[session_id] = self._clock()
        return True

    def count(self) -> int:
        return len(self._last_used)

    def _drop_expired(self) -> None:
        now = self._clock()
        limit = SESSION_IDLE_LIMIT.total_seconds()
        self._last_used = {session_id: used for session_id, used in self._last_used.items() if now - used < limit}
