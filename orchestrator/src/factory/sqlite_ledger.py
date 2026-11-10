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
            rows = conn.execute("SELECT task_id, cost_usd, assumed FROM ledger ORDER BY seq").fetchall()
        return [{"task_id": t, "cost_usd": c, "assumed": bool(a)} for t, c, a in rows]
