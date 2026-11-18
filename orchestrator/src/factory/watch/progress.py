from dataclasses import dataclass

from factory.watch.state import IssueRecord, WatchState, save_state


@dataclass(frozen=True)
class Progress:
    """One issue's persisted record. `save` writes the whole state file, not just this record."""

    state: WatchState
    state_dir: object
    record: IssueRecord

    def save(self) -> None:
        save_state(self.state_dir, self.state)
