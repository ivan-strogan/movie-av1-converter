"""
ffmpeg command builder and executor for a single conversion job.

Handles all four subtitle cases:
  1. No subtitles at all
  2. Embedded-only subtitles (copy or transcode depending on codec)
  3. External SRT-only (no embedded subs)
  4. Both embedded subs and external SRTs

Audio streams are always stream-copied.
Chapters and global metadata are always copied.
Output is written atomically via a .tmp.mkv intermediate.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import config
import db


# ── Public entry point ────────────────────────────────────────────────────────

def convert(row, dry_run: bool = False) -> bool:
    """
    Convert one job row (sqlite3.Row from the DB).
    Returns True on success, False on failure.
    """
    input_path  = Path(row["input_path"])
    output_path = Path(row["output_path"])
    tmp_path    = output_path.with_suffix(".tmp.mkv")

    # Probe input to determine subtitle codec handling
    probe = _ffprobe(input_path)
    if probe is None:
        db.mark_failed(input_path, "ffprobe failed before conversion")
        return False

    streams = probe.get("streams", [])
    sub_codec_arg = _subtitle_codec_arg(streams)
    external_srts = _find_external_srts(input_path)
    has_embedded_subs = _has_subtitles(streams)

    cmd = _build_command(
        input_path=input_path,
        output_path=tmp_path,
        external_srts=external_srts,
        has_embedded_subs=has_embedded_subs,
        sub_codec_arg=sub_codec_arg,
    )

    if dry_run:
        print("  " + " ".join(_quote(c) for c in cmd))
        return True

    # ── Ensure output directory exists ────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Remove any leftover tmp file ──────────────────────────────────────
    if tmp_path.exists():
        tmp_path.unlink()

    db.mark_in_progress(input_path)

    log_path = _log_path(input_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_path, "w", encoding="utf-8") as log_fh:
            log_fh.write("# Command:\n# " + " ".join(_quote(c) for c in cmd) + "\n\n")
            log_fh.flush()

            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=log_fh,
                # ffmpeg writes progress to stderr; we capture stdout silently
            )

        if proc.returncode != 0:
            _cleanup_tmp(tmp_path)
            error = f"ffmpeg exited with code {proc.returncode} — see {log_path}"
            db.mark_failed(input_path, error)
            return False

        # Atomic rename: tmp → final
        tmp_path.rename(output_path)
        return True

    except Exception as exc:
        _cleanup_tmp(tmp_path)
        db.mark_failed(input_path, str(exc))
        return False


# ── Command builder ───────────────────────────────────────────────────────────

def _build_command(
    input_path: Path,
    output_path: Path,
    external_srts: list[tuple[Path, str]],   # [(srt_path, lang_code), …]
    has_embedded_subs: bool,
    sub_codec_arg: str,                       # 'copy' or 'srt'
) -> list[str]:

    cmd = [config.FFMPEG_BIN, "-hide_banner", "-loglevel", "warning",
           "-stats", "-i", str(input_path)]

    # Additional SRT inputs
    for srt_path, _ in external_srts:
        cmd += ["-i", str(srt_path)]

    # ── Stream mapping ────────────────────────────────────────────────────
    if external_srts and has_embedded_subs:
        # Map all streams from input, then each external SRT
        cmd += ["-map", "0:v", "-map", "0:a", "-map", "0:s"]
        for idx in range(len(external_srts)):
            cmd += ["-map", f"{idx + 1}:s"]

    elif external_srts and not has_embedded_subs:
        # Input video + audio, external SRTs only
        cmd += ["-map", "0:v", "-map", "0:a"]
        for idx in range(len(external_srts)):
            cmd += ["-map", f"{idx + 1}:s"]

    else:
        # No external SRTs — map everything from input
        cmd += ["-map", "0"]

    # ── Video encoding ────────────────────────────────────────────────────
    cmd += [
        "-c:v", config.VIDEO_CODEC,
        "-crf", str(config.CRF),
        "-preset", str(config.PRESET),
        "-svtav1-params", config.SVTAV1_PARAMS,
    ]

    # ── Audio: stream copy ────────────────────────────────────────────────
    cmd += ["-c:a", "copy"]

    # ── Subtitles ─────────────────────────────────────────────────────────
    if external_srts or has_embedded_subs:
        cmd += ["-c:s", sub_codec_arg]

    # ── Language metadata for external SRTs ──────────────────────────────
    # Stream indices: 0=video, 1..N=audio, N+1..=subtitles
    # We only need to tag the *external* SRT streams; embedded ones keep
    # whatever metadata they already have.
    if has_embedded_subs and external_srts:
        # Count embedded sub streams to offset our indexing
        n_embedded_subs = sum(
            1 for s in _ffprobe_cache.get(str(input_path), {}).get("streams", [])
            if s.get("codec_type") == "subtitle"
        )
        for i, (_, lang) in enumerate(external_srts):
            sub_idx = n_embedded_subs + i
            cmd += [f"-metadata:s:s:{sub_idx}", f"language={lang}"]
    elif external_srts:
        for i, (_, lang) in enumerate(external_srts):
            cmd += [f"-metadata:s:s:{i}", f"language={lang}"]

    # ── Metadata and chapters ─────────────────────────────────────────────
    cmd += [
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-y",
        str(output_path),
    ]

    return cmd


# ── Subtitle helpers ──────────────────────────────────────────────────────────

def _has_subtitles(streams: list[dict]) -> bool:
    return any(s.get("codec_type") == "subtitle" for s in streams)


def _subtitle_codec_arg(streams: list[dict]) -> str:
    """
    Determine the -c:s argument needed.
    If any subtitle codec requires transcoding (e.g. mov_text → srt), return
    the target codec. Otherwise return 'copy'.
    Note: when mixing embedded + external SRTs with different codecs we default
    to 'srt' (SubRip) which MKV handles cleanly for all text-based subs.
    """
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        codec = stream.get("codec_name", "").lower()
        if codec in config.SUB_TRANSCODE:
            return config.SUB_TRANSCODE[codec]
    return "copy"


def _find_external_srts(video_path: Path) -> list[tuple[Path, str]]:
    """
    Find all SRT files in the same directory whose stem starts with the
    video file's stem (e.g. Movie.srt, Movie.en.srt, Movie.ru.srt).

    Returns a list of (Path, iso639_3_lang) tuples, sorted by filename.
    """
    stem = video_path.stem
    parent = video_path.parent
    results = []

    for srt in sorted(parent.glob(f"{_escape_glob(stem)}*.srt")):
        lang = _infer_lang(srt.stem, stem)
        results.append((srt, lang))

    return results


def _escape_glob(s: str) -> str:
    """Escape glob special characters in a filename stem."""
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def _infer_lang(srt_stem: str, video_stem: str) -> str:
    """
    Given a SRT stem like "Movie.en" and a video stem "Movie",
    extract the suffix ("en") and look it up in LANG_MAP.
    Falls back to "und" (undetermined) if not found.
    """
    suffix = srt_stem[len(video_stem):].lstrip(".")
    return config.LANG_MAP.get(suffix.lower(), "und")


# ── ffprobe ───────────────────────────────────────────────────────────────────

_ffprobe_cache: dict[str, dict] = {}


def _ffprobe(path: Path) -> Optional[dict]:
    key = str(path)
    if key in _ffprobe_cache:
        return _ffprobe_cache[key]

    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        _ffprobe_cache[key] = data
        return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


# ── Utilities ─────────────────────────────────────────────────────────────────

def _log_path(input_path: Path) -> Path:
    relative = input_path.relative_to(config.MOVIES_DIR)
    safe_name = str(relative).replace("/", "__").replace("\\", "__")
    return config.LOGS_DIR / (safe_name + ".log")


def _cleanup_tmp(tmp_path: Path) -> None:
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:
        pass


def _quote(s: str) -> str:
    """Shell-quote a string for display purposes only."""
    if re.search(r"[\s\"'\\]", s):
        return f'"{s}"'
    return s
