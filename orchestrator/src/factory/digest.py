import hashlib


def plan_digest(plan: str) -> str:
    return hashlib.sha256(plan.encode()).hexdigest()
