from pathlib import Path

from factory.sqlite_db import transaction


class SqliteLedger:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        with transaction(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ledger "
                "(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
                "cost_usd REAL NOT NULL, assumed INTEGER NOT NULL, stage TEXT)"
            )

    def append(
        self, task_id: str, cost_usd: float, assumed: bool = False, stage: str | None = None
    ) -> None:
        with transaction(self._db_path) as conn:
            conn.execute(
                "INSERT INTO ledger (task_id, cost_usd, assumed, stage) VALUES (?, ?, ?, ?)",
                (task_id, cost_usd, int(assumed), stage),
            )

    def entries(self) -> list[dict]:
        with transaction(self._db_path) as conn:
            rows = conn.execute("SELECT task_id, cost_usd, assumed, stage FROM ledger ORDER BY seq").fetchall()
        return [self._entry(*row) for row in rows]

    def total(self) -> float:
        with transaction(self._db_path) as conn:
            return conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM ledger").fetchone()[0]

    def would_exceed(self, cap_usd: float, next_budget_usd: float) -> bool:
        return self.total() + next_budget_usd > cap_usd

    @staticmethod
    def _entry(task_id: str, cost_usd: float, assumed: int, stage: str | None) -> dict:
        entry = {"task_id": task_id, "cost_usd": cost_usd, "assumed": bool(assumed)}
        if stage is not None:
            entry["stage"] = stage
        return entry
