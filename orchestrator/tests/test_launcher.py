import pytest

from factory.launcher import unsupported_reason


def test_windows_is_unsupported_with_a_message_saying_so():
    assert "Windows is not supported" in unsupported_reason("win32")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_macos_and_linux_are_supported(platform):
    assert unsupported_reason(platform) is None
