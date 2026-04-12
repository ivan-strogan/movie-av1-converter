"""
File discovery and database population.

Walk MOVIES_DIR, classify every file, and insert pending jobs into the DB.
Skipped files (DVD, already-AV1, unreadable) are logged to skipped_files.txt.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import config
import db


def ffprobe_info(path: Path) -> Optional[dict]:
    """
    Run ffprobe on *path* and return parsed JSON, or None on failure.
    """
    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=60
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def _video_stream(info: dict) -> Optional[dict]:
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def _output_path(input_path: Path) -> Path:
    """Mirror the input path under OUTPUT_DIR, with .mkv extension."""
    relative = input_path.relative_to(config.MOVIES_DIR)
    return config.OUTPUT_DIR / relative.with_suffix(".mkv")


def scan(dry_run: bool = False) -> None:
    """
    Walk MOVIES_DIR and populate the DB.
    In dry_run mode, print what would happen without writing to DB.
    """
    if not dry_run:
        db.init_db()

    counts = {"pending": 0, "skipped": 0, "ignored": 0, "error": 0}
    skipped_lines: list[str] = []

    print(f"Scanning {config.MOVIES_DIR} …")

    for path in sorted(config.MOVIES_DIR.rglob("*")):
        if not path.is_file():
            continue

        suffix = path.suffix.lower()

        # ── Completely ignore non-video support files ──────────────────────
        if suffix in config.IGNORE_EXTENSIONS:
            counts["ignored"] += 1
            continue

        # ── DVD structure files — skip + log ──────────────────────────────
        if suffix in config.DVD_EXTENSIONS:
            reason = {
                ".vob": "DVD VOB file - convert manually",
                ".ifo": "DVD navigation file - not a video",
                ".bup": "DVD backup file - not a video",
            }.get(suffix, "DVD-related file")
            _record_skip(path, reason, skipped_lines, dry_run)
            counts["skipped"] += 1
            continue

        # ── Video files to convert ─────────────────────────────────────────
        if suffix in config.CONVERT_EXTENSIONS:
            _process_video(path, skipped_lines, counts, dry_run)
            continue

        # ── Unknown extension — log as skipped ────────────────────────────
        reason = f"Unknown extension '{suffix}'"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["skipped"] += 1

    # ── Write skipped report ───────────────────────────────────────────────
    if not dry_run and skipped_lines:
        _write_skipped_report(skipped_lines)

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\nScan complete:")
    print(f"  Queued for conversion : {counts['pending']}")
    print(f"  Skipped               : {counts['skipped']}")
    print(f"  Ignored (non-video)   : {counts['ignored']}")
    print(f"  Errors (unreadable)   : {counts['error']}")

    if not dry_run:
        _write_scan_summary(counts)
        print(f"\nDatabase : {config.DB_PATH}")
        print(f"Reports  : {config.REPORTS_DIR}")


def _process_video(path: Path, skipped_lines: list,
                   counts: dict, dry_run: bool) -> None:
    """Probe a video file and either queue it or skip it."""
    info = ffprobe_info(path)

    if info is None:
        reason = "ffprobe failed - file may be corrupt or unsupported"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["error"] += 1
        return

    video_stream = _video_stream(info)
    if video_stream is None:
        reason = "No video stream found"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["skipped"] += 1
        return

    codec = video_stream.get("codec_name", "unknown").lower()
    if codec == "av1":
        reason = "Already AV1 - no conversion needed"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["skipped"] += 1
        return

    # ── Check if output already exists and is complete ────────────────────
    output_path = _output_path(path)
    if output_path.exists() and output_path.stat().st_size > 0:
        reason = f"Output already exists: {output_path}"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["skipped"] += 1
        return

    input_size  = path.stat().st_size
    fmt         = info.get("format", {})
    duration    = float(fmt.get("duration", 0) or 0)

    if dry_run:
        print(f"  [WOULD QUEUE] {path.name}  codec={codec}  "
              f"size={_human(input_size)}  dur={duration:.0f}s")
    else:
        db.upsert_pending(
            input_path=path,
            output_path=output_path,
            input_codec=codec,
            input_size=input_size,
            duration_secs=duration,
        )

    counts["pending"] += 1


def _record_skip(path: Path, reason: str,
                 skipped_lines: list, dry_run: bool) -> None:
    if dry_run:
        print(f"  [SKIP] {path}  ({reason})")
        return
    db.mark_skipped(path, reason)
    skipped_lines.append(f"{path}\t{reason}")


def _write_skipped_report(lines: list[str]) -> None:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS_DIR / "skipped_files.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Files skipped during scan\n")
        f.write("# Format: <path>\\t<reason>\n\n")
        for line in sorted(lines):
            f.write(line + "\n")
    print(f"Skipped report written to {out}")


def _write_scan_summary(counts: dict) -> None:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Gather per-codec and per-extension breakdown from DB
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    codec_rows = conn.execute("""
        SELECT input_codec, COUNT(*) as n,
               SUM(input_size) as total_bytes
        FROM conversions
        WHERE status = 'pending'
        GROUP BY input_codec ORDER BY n DESC
    """).fetchall()

    ext_rows = conn.execute("""
        SELECT LOWER(SUBSTR(input_path, INSTR(input_path, '.'), 10)) as ext,
               COUNT(*) as n
        FROM conversions
        WHERE status = 'pending'
        GROUP BY ext ORDER BY n DESC
    """).fetchall()
    conn.close()

    out = config.REPORTS_DIR / "scan_summary.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Scan Summary\n\n")
        f.write(f"Queued for conversion : {counts['pending']}\n")
        f.write(f"Skipped               : {counts['skipped']}\n")
        f.write(f"Ignored (non-video)   : {counts['ignored']}\n")
        f.write(f"Unreadable / errors   : {counts['error']}\n\n")

        f.write("## Source codec breakdown\n")
        for row in codec_rows:
            f.write(f"  {row['input_codec']:20s}  {row['n']:4d} files"
                    f"  {_human(row['total_bytes'] or 0)}\n")

        f.write("\n## Source extension breakdown\n")
        for row in ext_rows:
            f.write(f"  {row['ext']:10s}  {row['n']:4d} files\n")

    print(f"Scan summary written to {out}")


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
