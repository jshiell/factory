import re

from factory.ids import InvalidTaskId, require_safe_id

KINDS = ("plan", "result", "rejected", "changed")

# Budget for the whole rendered comment (marker, headings, fences, text, notes).
# GitHub's limit is 65,536; the margin absorbs anything the accounting misses.
MAX_COMMENT_CHARS = 60_000

_SEPARATOR = "\n\n"

# Room kept for each later section's fence and truncation note, so no heading is ever squeezed out.
_MIN_BLOCK = 120


def _section_overhead(title: str) -> int:
    return 2 * len(_SEPARATOR) + len(title)


def strip_home(text: str, home: str) -> str:
    return text.replace(home, "~")


def _marker_id(task_id: str) -> str:
    require_safe_id(task_id)
    if "--" in task_id:  # "--" is legal in a task id but not safe inside an HTML comment
        raise InvalidTaskId(task_id)
    return task_id


def _fence(text: str) -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest_run + 1)
    return f"{ticks}\n{text}\n{ticks}"


def _plain(text: str) -> str:
    return text


def _fit(text: str, room: int, wrap) -> str:
    """`wrap(text)` cut, before wrapping, so the result is at most `room` characters; a note marks the cut."""
    if len(wrap(text)) <= room:
        return wrap(text)
    omitted = len(text)
    cut = text
    while True:
        note = f"[truncated: {omitted - len(cut)} of {omitted} characters omitted]"
        shown = f"{cut}\n{note}" if cut else note
        excess = len(wrap(shown)) - room
        if excess <= 0 or not cut:
            return wrap(shown)
        cut = cut[: max(0, len(cut) - excess)]


def marker(task_id: str, kind: str) -> str:
    """The first line of every watcher comment; it identifies the task and the kind of comment."""
    if kind not in KINDS:
        raise ValueError(f"unknown comment kind: {kind!r}")
    return f"<!-- factory:task={_marker_id(task_id)}:kind={kind} -->"


def has_marker(body: str, task_id: str, kind: str) -> bool:
    """The body's first line is exactly the marker; a marker anywhere else (in a fence, after text) does not count."""
    return body.split("\n", 1)[0] == marker(task_id, kind)


def render_comment(task_id: str, kind: str, sections: list[tuple[str, str, bool]]) -> str:
    """sections: ordered (heading, text, fenced). Fenced text is inert: no mention, reference, image or marker in it acts."""
    parts = [marker(task_id, kind)]
    used = len(parts[0])
    titles = [f"**{heading}**" for heading, _, _ in sections]
    for index, (_, text, fenced) in enumerate(sections):
        later = sum(_section_overhead(title) + _MIN_BLOCK for title in titles[index + 1 :])
        room = MAX_COMMENT_CHARS - used - later - _section_overhead(titles[index])
        block = _fit(text, room, _fence if fenced else _plain)
        parts += [titles[index], block]
        used += _section_overhead(titles[index]) + len(block)
    return _SEPARATOR.join(parts)
