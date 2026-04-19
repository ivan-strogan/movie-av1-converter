"""
File discovery and database population.

Walk MOVIES_DIR, classify every file, and insert pending jobs into the DB.
Skipped files (DVD, already-AV1, unreadable) are logged to skipped_files.txt.
"""

import json
import os
import subprocess
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
    total_files = 0

    print(f"Scanning {config.MOVIES_DIR} …")

    for dirpath, _dirs, filenames in os.walk(config.MOVIES_DIR):
      for filename in filenames:
        path = Path(dirpath) / filename
        if not path.is_file():
            continue

        total_files += 1
        suffix = path.suffix.lower()

        # ── Completely ignore non-video support files ──────────────────────
        if suffix in config.IGNORE_EXTENSIONS:
            counts["ignored"] += 1
            _print_progress(total_files, counts, path.name)
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
            _print_progress(total_files, counts, path.name)
            continue

        # ── Video files to convert ─────────────────────────────────────────
        if suffix in config.CONVERT_EXTENSIONS:
            _process_video(path, skipped_lines, counts, dry_run)
            _print_progress(total_files, counts, path.name)
            continue

        # ── Unknown extension — log as skipped ────────────────────────────
        reason = f"Unknown extension '{suffix}'"
        _record_skip(path, reason, skipped_lines, dry_run)
        counts["skipped"] += 1
        _print_progress(total_files, counts, path.name)

    # ── Write skipped report ───────────────────────────────────────────────
    if not dry_run and skipped_lines:
        _write_skipped_report(skipped_lines)

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\r{' ' * 100}\r", end="")   # clear the progress line
    print(f"Scan complete:")
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

    input_size  = path.stat().st_size
    fmt         = info.get("format", {})
    duration    = float(fmt.get("duration", 0) or 0)
    output_path = _output_path(path)

    # ── Check if output already exists and is complete ────────────────────
    if output_path.exists() and output_path.stat().st_size > 0:
        if dry_run:
            print(f"  [ALREADY DONE] {path.name}")
        else:
            db.upsert_done(
                input_path=path,
                output_path=output_path,
                input_codec=codec,
                input_size=input_size,
                duration_secs=duration,
                output_size=output_path.stat().st_size,
            )
        counts["done"] = counts.get("done", 0) + 1
        return

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

    path_rows = conn.execute("""
        SELECT input_path FROM conversions WHERE status = 'pending'
    """).fetchall()
    conn.close()

    # Count extensions in Python so Path.suffix correctly handles paths
    # like ".../Spider-Man 2.1/movie.mp4" (SQL INSTR finds the first dot
    # in the whole string, producing garbage like ".1/spider-man..." as ext)
    from collections import Counter
    ext_counts = Counter(
        Path(r["input_path"]).suffix.lower() for r in path_rows
    )
    ext_rows = sorted(ext_counts.items(), key=lambda x: -x[1])

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
        for ext, n in ext_rows:
            f.write(f"  {ext:10s}  {n:4d} files\n")

    print(f"Scan summary written to {out}")


def _print_progress(total: int, counts: dict, current_name: str) -> None:
    name = current_name[:40].ljust(40)
    line = (f"  [{total:4d} files]  "
            f"queued={counts['pending']}  "
            f"skipped={counts['skipped']}  "
            f"ignored={counts['ignored']}  "
            f"errors={counts['error']}  "
            f"  {name}")
    print(f"\r{line}", end="", flush=True)


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"
