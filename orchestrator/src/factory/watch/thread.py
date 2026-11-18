from dataclasses import dataclass


@dataclass(frozen=True)
class IssueThread:
    """One issue's GitHub side. Each method makes exactly one call, when asked: nothing is cached or prefetched."""

    _github: object
    number: int
    _self_id: int

    def comments(self):
        return self._github.comments(self.number)

    def comment(self, body: str) -> int:
        return self._github.comment(self.number, body)

    def reactions(self, comment_id: int):
        return self._github.reactions(comment_id)

    def own_comment_id(self, matches) -> int | None:
        """A comment this watcher posted before a crash lost its id; authorship is by numeric id, never login, so nobody else's comment is adopted."""
        return next((c.id for c in self.comments() if c.user_id == self._self_id and matches(c)), None)
