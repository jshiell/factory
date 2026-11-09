from factory.task import Task


def gaps_to_carry(task: Task) -> tuple[str, ...]:
    """What a kept-plan retry of `task` tells the new attempt about the audit that rejected it."""
    if task.failure_reason == "audit_rejected":
        return task.audit_findings
    return ()
