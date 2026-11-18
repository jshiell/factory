from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author_id: int
    author_login: str
    created_at: datetime


def parse_issues(raw: list[dict], approver_ids: set[int], watching_since: datetime) -> list[Issue]:
    """The issues to consider: open, not a pull request, by an approver, created since watching began."""
    issues = []
    for r in raw:
        created_at = datetime.fromisoformat(r["created_at"])
        if "pull_request" in r or r["state"] != "open":
            continue
        if r["user"]["id"] not in approver_ids or created_at < watching_since:
            continue
        issues.append(
            Issue(
                number=r["number"],
                title=r["title"],
                body=r["body"] or "",
                author_id=r["user"]["id"],
                author_login=r["user"]["login"],
                created_at=created_at,
            )
        )
    return issues


GOAL_LIMIT = 20_000


_GOAL_TRUNCATED = "\n[issue text truncated]"


def _goal(issue: Issue) -> str:
    goal = f"{issue.title}\n\n{issue.body}"
    if len(goal) <= GOAL_LIMIT:
        return goal
    return goal[: GOAL_LIMIT - len(_GOAL_TRUNCATED)] + _GOAL_TRUNCATED
