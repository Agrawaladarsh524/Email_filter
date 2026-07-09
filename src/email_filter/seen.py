"""SQLite record of processed message IDs.

Polling re-lists the same window every cycle, so without this a message would
be re-labelled and re-alerted on every pass.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    message_id TEXT PRIMARY KEY,
    category   TEXT,
    seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS action_counts (
    action_type TEXT,
    date_str TEXT,
    count INTEGER,
    PRIMARY KEY (action_type, date_str)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    task TEXT,
    deadline TEXT,
    priority TEXT,
    status TEXT DEFAULT 'pending'
);
"""


class SeenStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(sqlite3.connect(self._path)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def filter_new(self, message_ids: list[str]) -> list[str]:
        if not message_ids:
            return []
        placeholders = ",".join("?" * len(message_ids))
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                f"SELECT message_id FROM seen WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
        known = {r[0] for r in rows}
        return [mid for mid in message_ids if mid not in known]

    def mark(self, message_id: str, category: str | None) -> None:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO seen (message_id, category) VALUES (?, ?)",
                (message_id, category),
            )
            conn.commit()

    def count(self) -> int:
        with closing(sqlite3.connect(self._path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def counts_by_category(self) -> dict[str, int]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT COALESCE(category, 'unclassified'), COUNT(*) "
                "FROM seen GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        return {name: n for name, n in rows}

    def recent(self, limit: int = 50) -> list[dict[str, str]]:
        with closing(sqlite3.connect(self._path)) as conn:
            rows = conn.execute(
                "SELECT message_id, category, seen_at FROM seen "
                "ORDER BY seen_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"message_id": m, "category": c or "unclassified", "seen_at": t}
            for m, c, t in rows
        ]

    def increment_action_count(self, action_type: str, date_str: str) -> None:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "INSERT INTO action_counts (action_type, date_str, count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(action_type, date_str) DO UPDATE SET count = count + 1",
                (action_type, date_str),
            )
            conn.commit()

    def get_action_count(self, action_type: str, date_str: str) -> int:
        with closing(sqlite3.connect(self._path)) as conn:
            row = conn.execute(
                "SELECT count FROM action_counts WHERE action_type = ? AND date_str = ?",
                (action_type, date_str)
            ).fetchone()
            return row[0] if row else 0

    def add_task(self, message_id: str, task: str, deadline: str | None, priority: str) -> None:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute(
                "INSERT INTO tasks (message_id, task, deadline, priority) VALUES (?, ?, ?, ?)",
                (message_id, task, deadline, priority)
            )
            conn.commit()
