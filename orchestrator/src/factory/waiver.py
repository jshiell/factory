class InvalidWaiver(ValueError):
    pass


def remove_waived(notes: tuple[str, ...], waived: tuple[str, ...]) -> tuple[str, ...]:
    """The notes without the waived ones, one occurrence removed per waived entry."""
    remaining = list(notes)
    for note in waived:
        if note not in remaining:
            raise InvalidWaiver(f"cannot waive a note that is not there: {note}")
        remaining.remove(note)
    return tuple(remaining)
