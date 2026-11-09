def remove_waived(notes: tuple[str, ...], waived: tuple[str, ...]) -> tuple[str, ...]:
    """The notes without the waived ones, one occurrence removed per waived entry."""
    remaining = list(notes)
    for note in waived:
        remaining.remove(note)
    return tuple(remaining)
