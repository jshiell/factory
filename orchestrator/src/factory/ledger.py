import json
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        self._path = path

    def append(self, task_id: str, cost_usd: float) -> None:
        entry = {"task_id": task_id, "cost_usd": cost_usd}
        with self._path.open("a") as ledger_file:
            ledger_file.write(json.dumps(entry) + "\n")

    def total(self) -> float:
        if not self._path.exists():
            return 0.0
        lines = self._path.read_text().splitlines()
        return sum(json.loads(line)["cost_usd"] for line in lines)

    def would_exceed(self, cap_usd: float, next_budget_usd: float) -> bool:
        return self.total() + next_budget_usd > cap_usd
