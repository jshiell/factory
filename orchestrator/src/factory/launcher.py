"""The console-script entry point. It imports nothing but `sys` so it can refuse a platform
before `factory.cli` pulls in POSIX-only modules (`fcntl`)."""


def unsupported_reason(platform: str) -> str | None:
    if platform == "win32":
        return "Windows is not supported; factory needs macOS or Linux"
    return None
