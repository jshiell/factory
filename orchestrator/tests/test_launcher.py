import subprocess
import sys

import pytest

from factory.launcher import run, unsupported_reason

PRETEND_WINDOWS_WITHOUT_FCNTL = (
    "import sys; sys.modules['fcntl'] = None; sys.platform = 'win32'; from factory.launcher import run; run()"
)


def test_windows_is_unsupported_with_a_message_saying_so():
    assert "Windows is not supported" in unsupported_reason("win32")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_macos_and_linux_are_supported(platform):
    assert unsupported_reason(platform) is None


def test_on_windows_the_launcher_refuses_before_loading_the_cli_or_fcntl():
    # a fresh interpreter: tests that already imported factory.cli cannot hide a regression
    ran = subprocess.run([sys.executable, "-I", "-c", PRETEND_WINDOWS_WITHOUT_FCNTL], capture_output=True, text=True)

    assert ran.returncode == 2
    assert ran.stderr == "unsupported platform: Windows is not supported; factory needs macOS or Linux\n"
    assert ran.stdout == ""


def test_on_a_supported_platform_the_launcher_runs_the_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("factory.cli.run", lambda: calls.append("ran"))

    run()

    assert calls == ["ran"]
