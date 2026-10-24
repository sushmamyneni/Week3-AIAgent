# The incident queue: one row per failure, in SQLite.
#
# It is what makes "one failure starts exactly one run" true. A failure is identified by its dedupe key
# (system, name, run id), the key is the primary key of the table, and INSERT OR IGNORE turns a repeated event
# into nothing. The same file is shared by the watcher (which adds rows), the worker (which runs them) and the
# inbox (which shows them), possibly from different processes, so every change is one short transaction.
#
# A row moves through:  queued -> running -> waiting_approval -> done
#                                     \-> failed -> (requeue) -> queued
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from failure_source import dedupe_key, name_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    dedupe_key  TEXT PRIMARY KEY,
    incident_id TEXT, system TEXT, name TEXT,
    event       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_approval', 'done', 'failed')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    thread_id   TEXT DEFAULT '',
    outcome     TEXT DEFAULT '',
    error       TEXT DEFAULT '',
    created_at  TEXT, started_at TEXT, finished_at TEXT
)"""

now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")

class IncidentQueue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as c:
            c.execute(SCHEMA)

    # isolation_level=None: no hidden transactions, so claim_next can use BEGIN IMMEDIATE itself.
    # timeout: wait up to 30 s if another process is writing, instead of failing.
    def _connect(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # Add a failure. Returns True if it is new, False if this exact failure was already queued: the duplicate
    # check lives in the database, so it holds even if two watchers run at once.
    def enqueue(self, event: dict) -> bool:
        with closing(self._connect()) as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO incidents (dedupe_key, incident_id, system, name, event, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
                (dedupe_key(event), event.get("incident_id", ""), event["system"], name_of(event), json.dumps(event), now()))
            return cur.rowcount == 1

    # Take the oldest queued failure and mark it running, in one transaction, so two workers can never take the
    # same one. Returns None if there is nothing queued, or if max_running runs are already in progress.
    def claim_next(self, max_running: int):
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT COUNT(*) FROM incidents WHERE status = 'running'").fetchone()[0] >= max_running:
                c.execute("ROLLBACK")
                return None
            row = c.execute("SELECT * FROM incidents WHERE status = 'queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return None
            attempts = row["attempts"] + 1
            # The checkpoint thread of the first attempt is the dedupe key; a retry after a crash gets its own thread,
            # so the half-finished first attempt cannot mix into it.
            thread_id = row["dedupe_key"] if attempts == 1 else f"{row['dedupe_key']}#attempt-{attempts}"
            c.execute("UPDATE incidents SET status = 'running', attempts = ?, thread_id = ?, started_at = ?, error = '' "
                      "WHERE dedupe_key = ?", (attempts, thread_id, now(), row["dedupe_key"]))
            c.execute("COMMIT")
            return {"dedupe_key": row["dedupe_key"], "incident_id": row["incident_id"], "event": json.loads(row["event"]),
                    "attempts": attempts, "thread_id": thread_id}

    def mark(self, key: str, status: str, outcome: str = "", error: str = ""):
        with closing(self._connect()) as c:
            c.execute("UPDATE incidents SET status = ?, outcome = ?, error = ?, finished_at = ? WHERE dedupe_key = ?",
                      (status, outcome, error[:300], now() if status in ("done", "failed") else None, key))

    # A failed run can be queued again (the plan: "mark a crashed run failed, write nothing, allow re-queueing")
    def requeue(self, key: str) -> bool:
        with closing(self._connect()) as c:
            return c.execute("UPDATE incidents SET status = 'queued', error = '' WHERE dedupe_key = ? AND status = 'failed'",
                             (key,)).rowcount == 1

    # A row still 'running' long after it started means its worker died. Mark it failed so it does not look
    # alive forever. Returns the keys it changed.
    def recover_crashed(self, older_than_seconds: int) -> list:
        limit = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat(timespec="seconds")
        with closing(self._connect()) as c:
            keys = [r[0] for r in c.execute("SELECT dedupe_key FROM incidents WHERE status = 'running' AND started_at < ?", (limit,))]
            for key in keys:
                c.execute("UPDATE incidents SET status = 'failed', error = 'the worker stopped before this run finished', "
                          "finished_at = ? WHERE dedupe_key = ?", (now(), key))
            return keys

    def get(self, key: str):
        with closing(self._connect()) as c:
            row = c.execute("SELECT * FROM incidents WHERE dedupe_key = ?", (key,)).fetchone()
            return dict(row) if row else None

    def rows(self, status: str = None) -> list:
        with closing(self._connect()) as c:
            sql, args = ("SELECT * FROM incidents WHERE status = ? ORDER BY created_at, rowid", (status,)) if status \
                else ("SELECT * FROM incidents ORDER BY created_at, rowid", ())
            return [dict(r) for r in c.execute(sql, args)]

    def counts(self) -> dict:
        with closing(self._connect()) as c:
            return {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM incidents GROUP BY status")}