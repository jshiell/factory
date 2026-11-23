import sqlite3
import threading
from contextlib import closing

from factory.sqlite_db import transaction


def test_wal_switch_waits_for_a_connection_holding_the_write_lock(tmp_path):
    db_path = tmp_path / "factory.db"
    holder = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, holder.execute, ["COMMIT"])
    release.start()
    try:
        with transaction(db_path) as conn:
            conn.execute("CREATE TABLE t (x)")
    finally:
        release.join()
        holder.close()

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
