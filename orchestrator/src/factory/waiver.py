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
