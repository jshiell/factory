import unicodedata


def failure_detail(error: BaseException) -> str:
    """One escaped line describing a failure for a log: the error text plus the stderr of a failed subprocess."""
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    text = f"{type(error).__name__}: {error}" + (f" [{stderr.strip()}]" if stderr and stderr.strip() else "")
    return escape_control(text).replace("\n", "\\n")


def escape_control(text: str) -> str:
    """Make agent-written text safe to show: escape control characters, invisible format characters and lone surrogates."""
    out = []
    for char in text:
        if char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            code = ord(char)
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}")
        else:
            out.append(char)
    return "".join(out)
