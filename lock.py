"""
Cooperative file lock to prevent two machines from running the converter
against the same NAS output directory simultaneously.

Lock file: OUTPUT_DIR/.converter.lock
Contents : hostname, PID, and start timestamp so a stale lock is identifiable.
"""

import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import config

LOCK_FILE = config.OUTPUT_DIR / ".converter.lock"


def acquire(force: bool = False) -> bool:
    """
    Try to acquire the lock.

    If the lock already exists and force=False, print who holds it and
    return False. If force=True, remove the existing lock first.

    Returns True if the lock was acquired, False otherwise.
    """
    if LOCK_FILE.exists():
        info = _read_lock()
        if force:
            print(f"Force-unlocking lock held by: {info}")
            release()
        else:
            print(
                f"ERROR: Another instance is already running.\n"
                f"  Lock file : {LOCK_FILE}\n"
                f"  Held by   : {info}\n"
                f"\n"
                f"If that machine is no longer running, use --force-unlock to clear it."
            )
            return False

    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(
            f"hostname: {socket.gethostname()}\n"
            f"pid: {os.getpid()}\n"
            f"started: {datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
        return True
    except OSError as e:
        print(f"WARNING: Could not create lock file: {e}")
        # Non-fatal — output dir may not be accessible (dry-run, no NAS, etc.)
        return True


def release() -> None:
    """Remove the lock file if it exists."""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


def _read_lock() -> str:
    """Return a human-readable summary of who holds the lock."""
    try:
        return LOCK_FILE.read_text(encoding="utf-8").strip().replace("\n", "  ")
    except OSError:
        return "(unreadable)"
