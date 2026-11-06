import json
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        self._path = path

    def append(self, task_id: str, cost_usd: float, assumed: bool = False) -> None:
        entry = {"task_id": task_id, "cost_usd": cost_usd, "assumed": assumed}
        with self._path.open("a") as ledger_file:
            ledger_file.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines()]

    def total(self) -> float:
        return sum(entry["cost_usd"] for entry in self.entries())

    def would_exceed(self, cap_usd: float, next_budget_usd: float) -> bool:
        return self.total() + next_budget_usd > cap_usd
