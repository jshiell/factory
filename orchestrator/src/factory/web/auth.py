import secrets
from datetime import timedelta

CODE_LIFETIME = timedelta(minutes=5)


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
    """Browser sessions, held in memory so a restart logs everyone out."""

    def __init__(self):
        self._ids = set()

    def create(self) -> str:
        session_id = secrets.token_urlsafe(32)
        self._ids.add(session_id)
        return session_id

    def valid(self, session_id: str) -> bool:
        return session_id in self._ids
