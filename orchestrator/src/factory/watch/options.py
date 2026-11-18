from dataclasses import dataclass


@dataclass(frozen=True)
class Options:
    """What one pass is configured with: who may approve, and the home directory stripped from posted text."""

    approver_ids: set[int]
    home: str
