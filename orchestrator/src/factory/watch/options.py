from dataclasses import dataclass


@dataclass(frozen=True)
class Options:
    """What one pass is configured with: who may approve, the home directory stripped from posted text, and the repo named in hints."""

    approver_ids: set[int]
    home: str
    repo: str
