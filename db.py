"""
SQLite interface for tracking conversion job state.

Status lifecycle:
  pending → in_progress → done
                        → failed
  pending → skipped
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversions (
                id              INTEGER PRIMARY KEY,
                input_path      TEXT    UNIQUE NOT NULL,
                output_path     TEXT,
                status          TEXT    NOT NULL DEFAULT 'pending',
                skip_reason     TEXT,
                input_codec     TEXT,
                input_size      INTEGER,
                output_size     INTEGER,
                duration_secs   REAL,
                started_at      TEXT,
                completed_at    TEXT,
                error_message   TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON conversions(status)
        """)


# ── Write helpers ─────────────────────────────────────────────────────────────

def upsert_pending(input_path: Path, output_path: Path,
                   input_codec: str, input_size: int,
                   duration_secs: float) -> None:
    """Insert a new pending job, or leave existing row untouched."""
    with _connect() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO conversions
                (input_path, output_path, status, input_codec, input_size, duration_secs)
            VALUES (?, ?, 'pending', ?, ?, ?)
        """, (str(input_path), str(output_path), input_codec, input_size, duration_secs))


def mark_skipped(input_path: Path, reason: str) -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO conversions (input_path, status, skip_reason)
            VALUES (?, 'skipped', ?)
        """, (str(input_path), reason))


def mark_in_progress(input_path: Path) -> None:
    with _connect() as conn:
        conn.execute("""
            UPDATE conversions
            SET status = 'in_progress',
                started_at = ?,
                error_message = NULL
            WHERE input_path = ?
        """, (_now(), str(input_path)))


def mark_done(input_path: Path, output_size: int) -> None:
    with _connect() as conn:
        conn.execute("""
            UPDATE conversions
            SET status = 'done',
                output_size = ?,
                completed_at = ?
            WHERE input_path = ?
        """, (output_size, _now(), str(input_path)))


def mark_failed(input_path: Path, error: str) -> None:
    with _connect() as conn:
        conn.execute("""
            UPDATE conversions
            SET status = 'failed',
                completed_at = ?,
                error_message = ?
            WHERE input_path = ?
        """, (_now(), error, str(input_path)))


def reset_in_progress() -> int:
    """
    Any job left as in_progress means a previous run was interrupted mid-encode.
    Reset those back to pending so they are retried.
    Returns count of rows reset.
    """
    with _connect() as conn:
        cur = conn.execute("""
            UPDATE conversions SET status = 'pending', started_at = NULL
            WHERE status = 'in_progress'
        """)
        return cur.rowcount


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_pending(limit: Optional[int] = None):
    """Yield rows with status='pending', ordered by input_path."""
    sql = "SELECT * FROM conversions WHERE status = 'pending' ORDER BY input_path"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _connect() as conn:
        yield from conn.execute(sql)


def get_status_counts() -> dict:
    with _connect() as conn:
        rows = conn.execute("""
            SELECT status, COUNT(*) as n FROM conversions GROUP BY status
        """).fetchall()
    return {row["status"]: row["n"] for row in rows}


def get_failed():
    with _connect() as conn:
        yield from conn.execute("""
            SELECT * FROM conversions WHERE status = 'failed' ORDER BY input_path
        """)


def get_skipped():
    with _connect() as conn:
        yield from conn.execute("""
            SELECT * FROM conversions WHERE status = 'skipped' ORDER BY input_path
        """)


def total_size_saved() -> tuple[int, int]:
    """Returns (total_input_bytes, total_output_bytes) for completed jobs."""
    with _connect() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(input_size), 0) as inp,
                   COALESCE(SUM(output_size), 0) as out
            FROM conversions WHERE status = 'done'
        """).fetchone()
    return row["inp"], row["out"]


# ── Internal ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
