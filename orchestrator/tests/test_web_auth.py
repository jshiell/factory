from datetime import datetime, timedelta

from factory.web.auth import LoginCodes


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 19, 12, 0, 0)

    def __call__(self):
        return self.now


def test_issued_code_redeems_once():
    codes = LoginCodes(FakeClock())
    code = codes.issue()
    assert codes.redeem(code) is True
    assert codes.redeem(code) is False


def test_unknown_code_is_refused():
    assert LoginCodes(FakeClock()).redeem("nope") is False


def test_code_expires_after_five_minutes():
    clock = FakeClock()
    codes = LoginCodes(clock)
    code = codes.issue()
    clock.now += timedelta(minutes=5)
    assert codes.redeem(code) is False


def test_code_is_valid_just_before_expiry():
    clock = FakeClock()
    codes = LoginCodes(clock)
    code = codes.issue()
    clock.now += timedelta(minutes=5) - timedelta(seconds=1)
    assert codes.redeem(code) is True


def test_issuing_prunes_expired_codes():
    clock = FakeClock()
    codes = LoginCodes(clock)
    codes.issue()
    clock.now += timedelta(minutes=6)
    codes.issue()
    assert len(codes._expiries) == 1
