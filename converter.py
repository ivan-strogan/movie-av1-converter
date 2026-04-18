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
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import config
import db


# ── Public entry point ────────────────────────────────────────────────────────

def convert(row, dry_run: bool = False) -> tuple[bool, int]:
    """
    Convert one job row (sqlite3.Row from the DB).
    Returns (success, crf_used).
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
    source_codec  = row["input_codec"] or ""
    input_size    = row["input_size"] or 0
    duration_secs = float(row["duration_secs"] or 0)
    crf           = crf_for_source(source_codec, input_size, duration_secs)

    _video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    resolution    = (f"{_video_stream['width']}x{_video_stream['height']}"
                     if _video_stream.get("width") else "unknown")

    if duration_secs > 0:
        bitrate_kbps = int((input_size * 8) / (duration_secs * 1000))
    else:
        bitrate_kbps = 0

    cmd = _build_command(
        input_path=input_path,
        output_path=tmp_path,
        external_srts=external_srts,
        has_embedded_subs=has_embedded_subs,
        sub_codec_arg=sub_codec_arg,
        source_codec=source_codec,
        crf_override=crf,
    )

    if dry_run:
        print(f"  [CRF {crf} — codec={source_codec or 'unknown'}  bitrate~{bitrate_kbps} kbps]")
        print("  " + " ".join(_quote(c) for c in cmd))
        return True, crf

    # ── Ensure output directory exists ────────────────────────────────────
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Remove any leftover tmp file ──────────────────────────────────────
    if tmp_path.exists():
        tmp_path.unlink()

    db.mark_in_progress(input_path)

    log_path = _log_path(input_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    input_mb = input_size / (1024 * 1024)
    dur_min  = int(duration_secs // 60)
    print(f"  Source: {source_codec or 'unknown'}  "
          f"{resolution}  "
          f"{bitrate_kbps} kbps  "
          f"{input_mb:.0f} MB  "
          f"{dur_min}min  "
          f"starting CRF={crf}",
          flush=True)

    # ── Probe-encode a 10-min sample to tune CRF before full encode ───────
    # Only worth doing for files longer than 20 minutes.
    if duration_secs > 1200:
        crf = _probe_crf(input_path, source_codec, input_size,
                         duration_secs, crf, log_path)

    cmd = _build_command(
        input_path=input_path,
        output_path=tmp_path,
        external_srts=external_srts,
        has_embedded_subs=has_embedded_subs,
        sub_codec_arg=sub_codec_arg,
        source_codec=source_codec,
        crf_override=crf,
    )

    print(f"  Encoding full movie  CRF={crf}", flush=True)

    try:
        with open(log_path, "a", encoding="utf-8") as log_fh:
            log_fh.write(f"\n# Full encode — CRF {crf}\n")
            log_fh.write("# Command:\n# " + " ".join(_quote(c) for c in cmd) + "\n\n")
            log_fh.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,   # progress lines from -progress pipe:1
                stderr=log_fh,            # warnings/errors go to log
                text=True,
            )

            _run_with_progress(proc, duration_secs)

        if proc.returncode != 0:
            _cleanup_tmp(tmp_path)
            error = f"ffmpeg exited with code {proc.returncode} — see {log_path}"
            db.mark_failed(input_path, error)
            return False, crf

        # Clear progress line before caller prints result
        print(f"\r{' ' * 80}\r", end="", flush=True)

        # Atomic rename: tmp → final
        tmp_path.rename(output_path)
        return True, crf

    except Exception as exc:
        _cleanup_tmp(tmp_path)
        db.mark_failed(input_path, str(exc))
        return False, crf


# ── Command builder ───────────────────────────────────────────────────────────

def crf_for_source(codec: str, input_size: int, duration_secs: float) -> int:
    """
    Select CRF based on source codec and estimated bitrate.
    Ensures the AV1 output targets a bitrate lower than the source,
    so files don't grow even for already-compressed sources.
    """
    # Estimate total bitrate in kbps from file size and duration
    if duration_secs > 0:
        bitrate_kbps = (input_size * 8) / (duration_secs * 1000)
    else:
        bitrate_kbps = 4000   # assume mid-range if unknown

    # Pick base CRF from bitrate tier
    base_crf = config.CRF_BITRATE_MAX
    for threshold, crf in config.CRF_BITRATE_TIERS:
        if bitrate_kbps < threshold:
            base_crf = crf
            break

    # Apply codec efficiency offset
    offset = config.CRF_CODEC_OFFSET.get(codec.lower(), 0)
    crf = base_crf + offset

    return max(config.CRF_MIN, min(crf, config.CRF_MAX))


def crf_for_codec(codec: str) -> int:
    """Convenience wrapper using only codec (no bitrate info). Used for display."""
    return crf_for_source(codec, 0, 0)


def _build_command(
    input_path: Path,
    output_path: Path,
    external_srts: list[tuple[Path, str]],   # [(srt_path, lang_code), …]
    has_embedded_subs: bool,
    sub_codec_arg: str,                       # 'copy' or 'srt'
    source_codec: str = "",
    crf_override: int = 0,
) -> list[str]:

    cmd = [config.FFMPEG_BIN, "-hide_banner", "-loglevel", "warning",
           "-progress", "pipe:1", "-nostats", "-i", str(input_path)]

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
    crf = crf_override or crf_for_source(source_codec, 0, 0)
    cmd += [
        "-c:v", config.VIDEO_CODEC,
        "-crf", str(crf),
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


def _probe_crf(
    input_path: Path,
    source_codec: str,
    input_size: int,
    duration_secs: float,
    initial_crf: int,
    log_path: Path,
) -> int:
    """
    Encode a 10-minute sample from ~30% into the file to check whether
    the current CRF will produce a smaller output than the source.

    If the sample grows (ratio > 1.0), increase CRF by 4 and retry.
    Repeats up to 4 times. Returns the best CRF found.
    Cleans up all temp files on exit.
    """
    # Ten 1-min clips spread evenly at 5%, 15%, 25% ... 95% through the movie.
    # Total sample = 10 min with full coverage, catching complex scenes that
    # 2-clip sampling would miss (critical for animation and varied content).
    clip_dur  = 60   # 1 minute each
    positions = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    clip_starts = [int(duration_secs * p) for p in positions]

    # Bytes-per-second of the source (approx) for the total sample window
    bps = input_size / duration_secs
    sample_source_size = bps * (clip_dur * len(positions))

    stem = input_path.stem[:40]
    tmp_dir = Path("/tmp")
    clips = [tmp_dir / f"_probe_clip{i}_{stem}.mkv" for i in range(len(positions))]
    encs  = [tmp_dir / f"_probe_enc{i}_{stem}.mkv"  for i in range(len(positions))]

    crf = initial_crf
    chosen_crf = initial_crf

    def _status(msg: str) -> None:
        print(f"  {msg}", flush=True)

    try:
        pct_labels = [f"{int(p*100)}%" for p in positions]
        _status(f"Probe: extracting 10 x 1min samples  "
                f"({', '.join(pct_labels)})")

        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n# Probe extract: positions={pct_labels}, each {clip_dur}s\n")

        # Extract all clips via stream copy (fast)
        for start, clip in zip(clip_starts, clips):
            result = subprocess.run([
                config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
                "-ss", str(start), "-t", str(clip_dur),
                "-i", str(input_path),
                "-map", "0:v", "-map", "0:a?",
                "-c", "copy",
                "-y", str(clip),
            ], capture_output=True, timeout=120)
            if result.returncode != 0 or not clip.exists():
                _status("Probe: clip extraction failed -- skipping probe, using initial CRF")
                return crf

        _status(f"Probe: samples ready  (10 x 1min = 10min total)")

        for attempt in range(8):
            _status(f"Probe: attempt {attempt + 1}/8  CRF={crf}")

            # Encode all clips separately, sum sizes
            total_enc_size = 0
            encode_ok = True
            for clip_label, clip, enc in zip(pct_labels, clips, encs):
                _status(f"Probe: encoding clip {clip_label}  CRF={crf}")
                enc_cmd = [
                    config.FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
                    "-progress", "pipe:1", "-nostats",
                    "-i", str(clip),
                    "-map", "0:v", "-map", "0:a?",
                    "-c:v", config.VIDEO_CODEC,
                    "-crf", str(crf),
                    "-preset", str(config.PRESET),
                    "-svtav1-params", config.SVTAV1_PARAMS,
                    "-c:a", "copy",
                    "-y", str(enc),
                ]
                with open(log_path, "a", encoding="utf-8") as lf:
                    proc = subprocess.Popen(
                        enc_cmd,
                        stdout=subprocess.PIPE,
                        stderr=lf,
                        text=True,
                    )
                _run_with_progress(proc, clip_dur)
                print()   # newline after progress bar
                if proc.returncode != 0 or not enc.exists():
                    encode_ok = False
                    break
                total_enc_size += enc.stat().st_size

            if not encode_ok:
                _status("Probe: encode failed -- using current CRF")
                break

            ratio   = total_enc_size / sample_source_size if sample_source_size > 0 else 1.0
            src_mb  = sample_source_size / (1024 * 1024)
            enc_mb  = total_enc_size / (1024 * 1024)

            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"# Probe attempt {attempt + 1}: CRF={crf}  "
                         f"ratio={ratio:.3f}  enc={total_enc_size//1024}KB  "
                         f"src~={int(sample_source_size)//1024}KB\n")

            target_pct = int(config.PROBE_TARGET_RATIO * 100)
            if ratio <= config.PROBE_TARGET_RATIO:
                _status(f"Probe: sample OK  "
                        f"ratio={ratio:.2f}  ({enc_mb:.0f}MB vs ~{src_mb:.0f}MB source)  "
                        f"CRF={crf} accepted")
                chosen_crf = crf
                break

            # Proportional CRF step: file size scales ~2^(-CRF/6), so the
            # exact delta needed is 6 * log2(ratio / target). This gives a
            # large bump when far from target and a small nudge when close.
            delta = 6.0 * math.log2(ratio / config.PROBE_TARGET_RATIO)
            if round(delta) == 0:
                # Within the noise floor (< 0.5 CRF steps needed) -- accept.
                _status(f"Probe: close enough  "
                        f"ratio={ratio:.2f}  ({enc_mb:.0f}MB vs ~{src_mb:.0f}MB source)  "
                        f"CRF={crf} accepted")
                chosen_crf = crf
                break
            delta = max(1, round(delta))
            next_crf = min(crf + delta, config.CRF_MAX)
            _status(f"Probe: sample too large  "
                    f"ratio={ratio:.2f}  ({enc_mb:.0f}MB vs ~{src_mb:.0f}MB source)  "
                    f"need <{target_pct}%  ->  raising CRF {crf} -> {next_crf} (+{delta})")

            chosen_crf = next_crf
            crf = next_crf
            if crf >= config.CRF_MAX:
                _status(f"Probe: reached CRF_MAX ({config.CRF_MAX}) -- proceeding")
                break

            for p in encs:
                try:
                    p.unlink()
                except OSError:
                    pass

        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"# Probe selected CRF={chosen_crf}\n")

        return chosen_crf

    finally:
        for p in clips + encs:
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass


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


# ── Progress bar ─────────────────────────────────────────────────────────────

def _run_with_progress(proc: subprocess.Popen, duration_secs: float) -> None:
    """
    Read ffmpeg's -progress pipe:1 output and print a live progress bar.
    Blocks until the process exits.
    """
    fields: dict[str, str] = {}

    try:
        for line in proc.stdout:
            line = line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            fields[key] = val

            if key == "progress":   # ffmpeg emits this at the end of each update block
                _print_bar(fields, duration_secs)
                fields = {}
    except Exception:
        # Never let a progress bar bug kill the conversion —
        # terminate ffmpeg cleanly and re-raise so the caller marks it failed.
        proc.kill()
        proc.wait()
        raise

    proc.wait()


def _print_bar(fields: dict, duration_secs: float) -> None:
    try:
        out_time_us = int(fields.get("out_time_us", 0) or 0)
    except (ValueError, TypeError):
        out_time_us = 0
    speed_str   = fields.get("speed", "").replace("x", "")
    fps_str     = fields.get("fps", "0")

    elapsed_secs = out_time_us / 1_000_000

    if duration_secs > 0:
        pct = min(elapsed_secs / duration_secs, 1.0)
    else:
        pct = 0.0

    # Bar
    bar_width = 28
    filled = int(bar_width * pct)
    bar = "█" * filled + "░" * (bar_width - filled)

    # ETA
    try:
        speed = float(speed_str)
    except (ValueError, TypeError):
        speed = 0.0

    if speed > 0 and duration_secs > 0:
        remaining = (duration_secs - elapsed_secs) / speed
        eta = f"ETA {_fmt_time(remaining)}"
    else:
        eta = "ETA --:--"

    fps = fps_str if fps_str and fps_str != "0" else "?"
    speed_label = f"{speed:.1f}x" if speed > 0 else "?x"

    line = f"  [{bar}] {pct*100:5.1f}%  {eta}  {speed_label}  {fps} fps"
    print(f"\r{line:<72}", end="", flush=True)


def _fmt_time(secs: float) -> str:
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m:02d}m{s:02d}s"


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
