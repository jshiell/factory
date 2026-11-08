MAX_ITEMS = 10
MAX_ITEM = 500
MAX_TOTAL = 4000
TRUNCATED = "[truncated]"


def capped_findings(findings: tuple[str, ...]) -> tuple[str, ...]:
    """One-line, bounded copies of the review findings; the same text is shown to the human and sent to the agents."""
    kept: list[str] = []
    total = 0
    cut = False
    for raw in findings:
        line = " ".join(raw.split())
        if not line:
            continue
        if len(line) > MAX_ITEM:
            line, cut = line[:MAX_ITEM], True
        if len(kept) == MAX_ITEMS or total + len(line) > MAX_TOTAL:
            cut = True
            break
        kept.append(line)
        total += len(line)
    return (*kept, TRUNCATED) if cut else tuple(kept)
