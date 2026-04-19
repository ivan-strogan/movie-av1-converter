"""
SQLite interface for tracking conversion job state.

Status lifecycle:
  pending → in_progress → done
                        → failed
  pending → skipped
"""

import shutil
import socket
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _exec(sql: str, params: tuple = ()) -> None:
    """Execute a single write statement and close the connection."""
    conn = _connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist, and migrate older schemas."""
    conn = _connect()
    try:
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
                error_message   TEXT,
                crf_used        INTEGER,
                encoded_by      TEXT,
                encode_secs     REAL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON conversions(status)
        """)
        # Migrate older databases that pre-date these columns
        for col_sql in [
            "ALTER TABLE conversions ADD COLUMN crf_used    INTEGER",
            "ALTER TABLE conversions ADD COLUMN encoded_by  TEXT",
            "ALTER TABLE conversions ADD COLUMN encode_secs REAL",
        ]:
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
    finally:
        conn.close()


# ── Write helpers ─────────────────────────────────────────────────────────────

def upsert_pending(input_path: Path, output_path: Path,
                   input_codec: str, input_size: int,
                   duration_secs: float) -> None:
    """Insert a new pending job; if row already exists, fill in any NULL metadata."""
    _exec("""
        INSERT INTO conversions
            (input_path, output_path, status, input_codec, input_size, duration_secs)
        VALUES (?, ?, 'pending', ?, ?, ?)
        ON CONFLICT(input_path) DO UPDATE SET
            output_path   = COALESCE(conversions.output_path,   excluded.output_path),
            input_codec   = COALESCE(conversions.input_codec,   excluded.input_codec),
            input_size    = COALESCE(conversions.input_size,    excluded.input_size),
            duration_secs = COALESCE(conversions.duration_secs, excluded.duration_secs)
    """, (str(input_path), str(output_path), input_codec, input_size, duration_secs))


def upsert_done(input_path: Path, output_path: Path,
                input_codec: str, input_size: int,
                duration_secs: float, output_size: int) -> None:
    """Insert or update a row as done (output already exists on disk)."""
    _exec("""
        INSERT INTO conversions
            (input_path, output_path, status, input_codec, input_size,
             duration_secs, output_size, completed_at)
        VALUES (?, ?, 'done', ?, ?, ?, ?, ?)
        ON CONFLICT(input_path) DO UPDATE SET
            status       = 'done',
            output_path  = excluded.output_path,
            output_size  = excluded.output_size,
            completed_at = excluded.completed_at
    """, (str(input_path), str(output_path), input_codec, input_size,
          duration_secs, output_size, _now()))


def mark_skipped(input_path: Path, reason: str) -> None:
    _exec("""
        INSERT OR REPLACE INTO conversions (input_path, status, skip_reason)
        VALUES (?, 'skipped', ?)
    """, (str(input_path), reason))


def mark_in_progress(input_path: Path) -> None:
    _exec("""
        UPDATE conversions
        SET status = 'in_progress',
            started_at = ?,
            encoded_by = ?,
            error_message = NULL
        WHERE input_path = ?
    """, (_now(), socket.gethostname(), str(input_path)))


def mark_done(input_path: Path, output_size: int,
              crf_used: int = 0, encode_secs: float = 0.0) -> None:
    _exec("""
        UPDATE conversions
        SET status = 'done',
            output_size  = ?,
            completed_at = ?,
            crf_used     = ?,
            encode_secs  = ?
        WHERE input_path = ?
    """, (output_size, _now(),
          crf_used or None, encode_secs or None,
          str(input_path)))


def mark_failed(input_path: Path, error: str) -> None:
    _exec("""
        UPDATE conversions
        SET status = 'failed',
            completed_at = ?,
            error_message = ?
        WHERE input_path = ?
    """, (_now(), error, str(input_path)))


def reset_failed() -> int:
    """Reset all failed jobs back to pending so they are retried."""
    conn = _connect()
    try:
        cur = conn.execute("""
            UPDATE conversions SET status = 'pending',
                started_at = NULL, completed_at = NULL, error_message = NULL
            WHERE status = 'failed'
        """)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def reset_in_progress() -> int:
    """
    Any job left as in_progress means a previous run was interrupted mid-encode.
    Reset those back to pending so they are retried.
    Returns count of rows reset.
    """
    conn = _connect()
    try:
        cur = conn.execute("""
            UPDATE conversions SET status = 'pending', started_at = NULL
            WHERE status = 'in_progress'
        """)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_pending(limit: Optional[int] = None):
    """Return all rows with status='pending', ordered by input_path."""
    sql = "SELECT * FROM conversions WHERE status = 'pending' ORDER BY input_path"
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = _connect()
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def get_any_matching(name: str):
    """Return rows (any status) whose input_path contains *name* (case-insensitive)."""
    conn = _connect()
    try:
        return conn.execute("""
            SELECT * FROM conversions
            WHERE LOWER(input_path) LIKE LOWER(?)
            ORDER BY input_path
        """, (f"%{name}%",)).fetchall()
    finally:
        conn.close()


def reset_to_pending(input_path: Path) -> None:
    """Force a single file back to pending regardless of current status."""
    _exec("""
        UPDATE conversions
        SET status = 'pending',
            started_at = NULL,
            completed_at = NULL,
            error_message = NULL,
            output_size = NULL
        WHERE input_path = ?
    """, (str(input_path),))


def get_pending_matching(name: str):
    """Return pending rows whose input_path contains *name* (case-insensitive)."""
    conn = _connect()
    try:
        return conn.execute("""
            SELECT * FROM conversions
            WHERE status = 'pending'
              AND LOWER(input_path) LIKE LOWER(?)
            ORDER BY input_path
        """, (f"%{name}%",)).fetchall()
    finally:
        conn.close()


def get_status_counts() -> dict:
    conn = _connect()
    try:
        rows = conn.execute("""
            SELECT status, COUNT(*) as n FROM conversions GROUP BY status
        """).fetchall()
        return {row["status"]: row["n"] for row in rows}
    finally:
        conn.close()


def get_failed():
    conn = _connect()
    try:
        return conn.execute("""
            SELECT * FROM conversions WHERE status = 'failed' ORDER BY input_path
        """).fetchall()
    finally:
        conn.close()


def get_skipped():
    conn = _connect()
    try:
        return conn.execute("""
            SELECT * FROM conversions WHERE status = 'skipped' ORDER BY input_path
        """).fetchall()
    finally:
        conn.close()


def total_size_saved() -> tuple[int, int]:
    """Returns (total_input_bytes, total_output_bytes) for completed jobs."""
    conn = _connect()
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(input_size), 0) as inp,
                   COALESCE(SUM(output_size), 0) as out
            FROM conversions WHERE status = 'done'
        """).fetchone()
        return row["inp"], row["out"]
    finally:
        conn.close()


# ── DB sync and reconciliation ────────────────────────────────────────────────

def sync_db() -> bool:
    """
    Copy the active DB to the backup location.
    NAS DB -> local copy, or local -> NAS if that's what's active.
    Returns True if the copy succeeded.
    """
    src = config.DB_PATH
    dst = (config.LOCAL_DB_PATH
           if src == config.NAS_DB_PATH
           else config.NAS_DB_PATH)

    if not src.exists():
        return False
    if dst == src:
        return True

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return True
    except OSError:
        return False


def reconcile() -> tuple[int, int]:
    """
    Reconcile the DB against what is actually on disk.

    1. Rows marked 'done' whose output file no longer exists -> reset to pending.
    2. Rows marked pending/failed/in_progress whose output already exists on
       disk -> mark done (handles files converted on another machine).

    Returns (reset_count, found_count).
    """
    conn = _connect()
    reset_count = 0
    found_count = 0

    try:
        # 1. Done rows where the output file is missing
        done_rows = conn.execute("""
            SELECT input_path, output_path FROM conversions WHERE status = 'done'
        """).fetchall()

        for row in done_rows:
            out = Path(row["output_path"])
            if not out.exists() or out.stat().st_size == 0:
                conn.execute("""
                    UPDATE conversions
                    SET status = 'pending',
                        output_size = NULL,
                        completed_at = NULL
                    WHERE input_path = ?
                """, (row["input_path"],))
                reset_count += 1

        # 2. Not-done rows where the output file already exists
        not_done = conn.execute("""
            SELECT input_path, output_path
            FROM conversions
            WHERE status IN ('pending', 'failed', 'in_progress')
              AND output_path IS NOT NULL
        """).fetchall()

        for row in not_done:
            out = Path(row["output_path"])
            if out.exists() and out.stat().st_size > 0:
                conn.execute("""
                    UPDATE conversions
                    SET status = 'done',
                        output_size = ?,
                        completed_at = ?
                    WHERE input_path = ?
                """, (out.stat().st_size, _now(), row["input_path"]))
                found_count += 1

        # 3. Rows skipped because output already existed at scan time -> mark done
        # output_path is NULL in skipped rows — extract it from skip_reason
        # which is stored as "Output already exists: /path/to/file.mkv"
        skipped_existing = conn.execute("""
            SELECT input_path, skip_reason
            FROM conversions
            WHERE status = 'skipped'
              AND skip_reason LIKE 'Output already exists:%'
        """).fetchall()

        for row in skipped_existing:
            out_str = row["skip_reason"].split("Output already exists:", 1)[-1].strip()
            out = Path(out_str)
            if out.exists() and out.stat().st_size > 0:
                conn.execute("""
                    UPDATE conversions
                    SET status = 'done',
                        output_path = ?,
                        skip_reason = NULL,
                        output_size = ?,
                        completed_at = ?
                    WHERE input_path = ?
                """, (str(out), out.stat().st_size, _now(), row["input_path"]))
                found_count += 1

        conn.commit()
    finally:
        conn.close()

    return reset_count, found_count


# ── Internal ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
