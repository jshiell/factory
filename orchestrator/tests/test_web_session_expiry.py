"""A browser session that goes unused for eight hours expires; each use starts the eight hours again."""
from datetime import timedelta

from factory.web.auth import SESSION_IDLE_LIMIT, Sessions


class FakeClock:
    def __init__(self):
        self.elapsed = timedelta(0)

    def __call__(self):
        return self.elapsed.total_seconds()

    def advance(self, by):
        self.elapsed += by


def test_the_idle_limit_is_eight_hours():
    assert timedelta(hours=8) == SESSION_IDLE_LIMIT


def test_a_session_unused_for_the_idle_limit_is_refused():
    clock = FakeClock()
    sessions = Sessions(clock)
    session_id = sessions.create()

    clock.advance(SESSION_IDLE_LIMIT - timedelta(seconds=1))
    assert sessions.valid(session_id) is True

    clock.advance(SESSION_IDLE_LIMIT)
    assert sessions.valid(session_id) is False


def test_each_use_restarts_the_idle_period():
    clock = FakeClock()
    sessions = Sessions(clock)
    session_id = sessions.create()

    for _ in range(4):
        clock.advance(timedelta(hours=7))
        assert sessions.valid(session_id) is True


def test_a_refused_session_stays_refused_even_if_the_clock_could_not_revive_it():
    clock = FakeClock()
    sessions = Sessions(clock)
    session_id = sessions.create()
    clock.advance(SESSION_IDLE_LIMIT + timedelta(seconds=1))
    sessions.valid(session_id)

    assert sessions.valid(session_id) is False


def test_expired_sessions_are_dropped_when_another_is_created():
    clock = FakeClock()
    sessions = Sessions(clock)
    for _ in range(3):
        sessions.create()
    clock.advance(SESSION_IDLE_LIMIT + timedelta(seconds=1))
    sessions.create()

    assert sessions.count() == 1


def test_an_unknown_id_is_refused():
    assert Sessions(FakeClock()).valid("nope") is False
