"""Immutable run register. SQLite for Work tests; PostgreSQL for deployed service."""
import json
import os
import sqlite3
from pathlib import Path


class Store:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def __init__(self, url=None):
        self.url = url or os.getenv("DATABASE_URL", "sqlite:///outputs/runs.sqlite")
        self.postgres = self.url.startswith(("postgresql://", "postgres://"))
        if self.postgres:
            import psycopg
            self.connection = psycopg.connect(self.url)
            self.connection.execute("CREATE TABLE IF NOT EXISTS model_run (run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload JSONB NOT NULL)")
        else:
            path = self.url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, check_same_thread=False)
            self.connection.execute("CREATE TABLE IF NOT EXISTS model_run (run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
        self.connection.commit()

    def insert(self, run):
        s = "%s" if self.postgres else "?"
        self.connection.execute(f"INSERT INTO model_run VALUES ({s},{s},{s})",
                                (run['run_id'], run['timestamp'], json.dumps(run,allow_nan=False)))
        self.connection.commit()

    def get(self, run_id):
        s = "%s" if self.postgres else "?"
        row = self.connection.execute(f"SELECT payload FROM model_run WHERE run_id={s}",(run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row[0] if isinstance(row[0],dict) else json.loads(row[0])

    def list(self, limit=100):
        rows = self.connection.execute("SELECT payload FROM model_run ORDER BY created_at DESC LIMIT " + str(min(100,max(1,int(limit))))).fetchall()
        return [r[0] if isinstance(r[0],dict) else json.loads(r[0]) for r in rows]
