import re


class InvalidTaskId(Exception):
    pass


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def require_safe_id(task_id: str) -> str:
    if not _SAFE_ID.fullmatch(task_id):
        raise InvalidTaskId(task_id)
    return task_id
