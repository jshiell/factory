from factory.task import Task

MAX_GAPS = 10
MAX_GAP = 500
MAX_TOTAL = 4000


def _bounded(findings: tuple[str, ...]) -> tuple[str, ...]:
    """One line each, cut to MAX_GAP; stops at MAX_GAPS lines or when MAX_TOTAL characters would be passed."""
    kept: list[str] = []
    total = 0
    for finding in findings:
        line = " ".join(finding.split())[:MAX_GAP]
        if not line:
            continue
        if len(kept) == MAX_GAPS or total + len(line) > MAX_TOTAL:
            break
        kept.append(line)
        total += len(line)
    return tuple(kept)


def gaps_to_carry(task: Task) -> tuple[str, ...]:
    """What a kept-plan retry of `task` tells the new attempt about the audit that rejected it."""
    if task.failure_reason == "audit_rejected":
        return _bounded(task.audit_findings)
    return ()


def split_quoting_waived(gaps: tuple[str, ...], waived: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(kept, dropped): a gap that quotes a waived note (case-insensitive) is dropped."""
    quotes = [w.lower() for w in waived]
    dropped = tuple(g for g in gaps if any(q in g.lower() for q in quotes))
    return tuple(g for g in gaps if g not in dropped), dropped
