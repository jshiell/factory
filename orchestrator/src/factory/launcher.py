"""The console-script entry point. It imports nothing but `sys` so it can refuse a platform
before `factory.cli` pulls in POSIX-only modules (`fcntl`)."""

import sys


def unsupported_reason(platform: str) -> str | None:
    if platform == "win32":
        return "Windows is not supported; factory needs macOS or Linux"
    return None


def run() -> None:
    reason = unsupported_reason(sys.platform)
    if reason:
        print(f"unsupported platform: {reason}", file=sys.stderr)
        sys.exit(2)
    from factory import cli

    cli.run()
