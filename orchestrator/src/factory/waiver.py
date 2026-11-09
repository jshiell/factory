from dataclasses import dataclass

from factory.review_notes import is_required


class InvalidWaiver(ValueError):
    pass


def remove_waived(notes: tuple[str, ...], waived: tuple[str, ...]) -> tuple[str, ...]:
    """The notes without the waived ones, one occurrence removed per waived entry; only required notes can be waived."""
    remaining = list(notes)
    for note in waived:
        if not is_required(note):
            raise InvalidWaiver(f"only a required note can be waived: {note}")
        if note not in remaining:
            raise InvalidWaiver(f"cannot waive a note that is not there: {note}")
        remaining.remove(note)
    return tuple(remaining)


@dataclass(frozen=True)
class Approve:
    pass


@dataclass(frozen=True)
class Refuse:
    reason: str | None


@dataclass(frozen=True)
class Waive:
    numbers: tuple[int, ...]


def parse_answer(text: str, required_count: int) -> Approve | Waive | Refuse:
    """What the human typed at the approval prompt: `y` approves, `w 1 3` approves and waives notes 1 and 3."""
    words = text.split()
    if words == ["y"]:
        return Approve()
    if words[:1] == ["w"]:
        if len(words) == 1:
            return Refuse(reason="w needs the numbers of the notes to waive")
        if not all(word.isascii() and word.isdigit() for word in words[1:]):
            return Refuse(reason="note numbers are plain digits, like: w 1 3")
        numbers = tuple(int(word) for word in words[1:])
        if not all(1 <= number <= required_count for number in numbers):
            return Refuse(reason=f"note numbers must be between 1 and {required_count}")
        if len(set(numbers)) != len(numbers):
            return Refuse(reason="a note number was given twice")
        return Waive(numbers=numbers)
    return Refuse(reason=None)


WAIVE_HINT = "(or 'w 1 3' to waive required notes 1 and 3) "


def note_lines(notes: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """The display lines for the notes, and the required notes in order; a required note is numbered for waiving."""
    lines, required = [], []
    for note in notes:
        if is_required(note):
            required.append(note)
            lines.append(f"  - [{len(required)}] {note}")
        else:
            lines.append(f"  - {note}")
    return lines, required


def approval_prompt(question: str, required: list[str]) -> str:
    return question + " " + WAIVE_HINT if required else question + " "
