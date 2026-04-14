#!/usr/bin/env python3
"""
movie-av1-converter — CLI entry point.

Commands:
  scan     [--dry-run]          Discover files and populate the DB.
  convert  [--dry-run] [--limit N]  Process pending jobs.
  status                        Show progress summary.
  report                        Regenerate skipped_files.txt and failure log.
"""

import argparse
import sys
import time
from pathlib import Path

import config
import db
import scanner
import converter
import verify


# ── scan ──────────────────────────────────────────────────────────────────────

def cmd_scan(args) -> None:
    scanner.scan(dry_run=args.dry_run)


# ── convert ───────────────────────────────────────────────────────────────────

def cmd_convert(args) -> None:
    db.init_db()

    # Re-queue any jobs that were left in_progress by a previous interrupted run
    reset = db.reset_in_progress()
    if reset:
        print(f"Re-queued {reset} interrupted job(s) from previous run.")

    # ── --file: single specific file ──────────────────────────────────────
    if args.file:
        rows = db.get_pending_matching(args.file)
        if not rows:
            print(f"No pending file matching '{args.file}'. "
                  f"Check the name or run 'python3 main.py status'.")
            return
        if len(rows) > 1:
            print(f"Multiple matches for '{args.file}' — be more specific:\n")
            for r in rows:
                print(f"  {r['input_path']}")
            return
        rows_to_process = rows
        todo = 1
        print(f"Converting 1 file (matched '{args.file}')\n")
    else:
        counts = db.get_status_counts()
        total_pending = counts.get("pending", 0)
        if total_pending == 0:
            print("No pending jobs. Run 'python3 main.py scan' first.")
            return
        limit = args.limit
        todo  = min(total_pending, limit) if limit else total_pending
        rows_to_process = db.get_pending(limit=limit)
        print(f"Converting {todo} file(s)  (pending={total_pending})\n")

    done = failed = 0
    start_wall = time.monotonic()

    for row in rows_to_process:
        input_path  = Path(row["input_path"])
        output_path = Path(row["output_path"])

        label = input_path.relative_to(config.MOVIES_DIR)
        print(f"[{done + failed + 1}/{todo}] {label}")

        if args.dry_run:
            converter.convert(row, dry_run=True)
            done += 1
            continue

        t0 = time.monotonic()
        ok = converter.convert(row, dry_run=False)
        elapsed = time.monotonic() - t0

        if not ok:
            failed += 1
            print(f"  FAILED  ({elapsed:.0f}s)")
            continue

        # ── Verify output ──────────────────────────────────────────────────
        src_audio = verify.count_audio_streams(input_path)
        ok_verify, reason = verify.verify(
            input_path=input_path,
            output_path=output_path,
            source_duration=row["duration_secs"] or 0,
            source_audio_count=src_audio,
        )

        if not ok_verify:
            # Delete corrupt output and mark failed
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass
            db.mark_failed(input_path, f"Verification failed: {reason}")
            failed += 1
            print(f"  VERIFY FAILED: {reason}  ({elapsed:.0f}s)")
            continue

        out_size = output_path.stat().st_size
        db.mark_done(input_path, out_size)
        done += 1

        ratio    = out_size / (row["input_size"] or 1)
        saved_mb = (row["input_size"] - out_size) / (1024 * 1024)
        print(f"  OK  {elapsed:.0f}s  ratio={ratio:.2f}  saved={saved_mb:.0f} MB")

    wall = time.monotonic() - start_wall
    inp_total, out_total = db.total_size_saved()
    print(f"\n{'─' * 60}")
    print(f"Done: {done}  Failed: {failed}  Wall time: {_fmt_dur(wall)}")
    if inp_total:
        saved = (inp_total - out_total) / (1024 ** 3)
        print(f"Space saved so far: {saved:.2f} GB  "
              f"(avg ratio {out_total/inp_total:.2f})")


# ── status ────────────────────────────────────────────────────────────────────

def cmd_status(args) -> None:
    db.init_db()
    counts = db.get_status_counts()

    total = sum(counts.values())
    print(f"{'Status':<15} {'Count':>6}  {'%':>5}")
    print("─" * 30)
    for status in ("pending", "in_progress", "done", "failed", "skipped"):
        n = counts.get(status, 0)
        pct = (n / total * 100) if total else 0
        print(f"{status:<15} {n:>6}  {pct:>5.1f}%")
    print("─" * 30)
    print(f"{'TOTAL':<15} {total:>6}")

    inp, out = db.total_size_saved()
    if inp:
        saved_gb = (inp - out) / (1024 ** 3)
        print(f"\nSpace saved (done jobs): {saved_gb:.2f} GB  "
              f"(avg compression ratio {out/inp:.2f})")


# ── report ────────────────────────────────────────────────────────────────────

def cmd_report(args) -> None:
    db.init_db()
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── skipped_files.txt ──────────────────────────────────────────────────
    skipped_path = config.REPORTS_DIR / "skipped_files.txt"
    with open(skipped_path, "w", encoding="utf-8") as f:
        f.write("# Skipped files\n# Format: <path>\\t<reason>\n\n")
        for row in db.get_skipped():
            f.write(f"{row['input_path']}\t{row['skip_reason']}\n")
    print(f"Skipped report: {skipped_path}")

    # ── failed_files.txt ──────────────────────────────────────────────────
    failed_path = config.REPORTS_DIR / "failed_files.txt"
    n_failed = 0
    with open(failed_path, "w", encoding="utf-8") as f:
        f.write("# Failed conversions\n# Format: <path>\\t<error>\n\n")
        for row in db.get_failed():
            f.write(f"{row['input_path']}\t{row['error_message']}\n")
            n_failed += 1
    print(f"Failed report  : {failed_path}  ({n_failed} entries)")

    # ── completion summary ─────────────────────────────────────────────────
    counts = db.get_status_counts()
    done    = counts.get("done", 0)
    total   = sum(counts.values())
    inp, out = db.total_size_saved()

    summary_path = config.REPORTS_DIR / "completion_summary.txt"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("# Completion Summary\n\n")
        for status, n in sorted(counts.items()):
            f.write(f"{status:<15} {n}\n")
        f.write(f"\nTotal files  : {total}\n")
        if inp:
            saved_gb = (inp - out) / (1024 ** 3)
            f.write(f"Space saved  : {saved_gb:.2f} GB\n")
            f.write(f"Avg ratio    : {out/inp:.2f}\n")
    print(f"Summary        : {summary_path}")


# ── CLI wiring ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Batch-convert movies to AV1 (MKV) using ffmpeg + SVT-AV1.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Discover files and populate DB")
    p_scan.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without writing to DB")

    # convert
    p_conv = sub.add_parser("convert", help="Process pending conversion jobs")
    p_conv.add_argument("--dry-run", action="store_true",
                        help="Print ffmpeg commands without executing")
    p_conv.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Stop after converting N files")
    p_conv.add_argument("--file", default=None, metavar="NAME",
                        help="Convert only the file whose path contains NAME (case-insensitive)")

    # status
    sub.add_parser("status", help="Show conversion progress")

    # report
    sub.add_parser("report", help="Regenerate skipped/failed/summary reports")

    args = parser.parse_args()

    dispatch = {
        "scan":    cmd_scan,
        "convert": cmd_convert,
        "status":  cmd_status,
        "report":  cmd_report,
    }
    dispatch[args.command](args)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_dur(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


if __name__ == "__main__":
    main()
