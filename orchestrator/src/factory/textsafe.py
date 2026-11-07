import unicodedata


def escape_control(text: str) -> str:
    """Make agent-written text safe to show: escape control and invisible format characters."""
    out = []
    for char in text:
        if char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf"):
            code = ord(char)
            out.append(f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}")
        else:
            out.append(char)
    return "".join(out)
