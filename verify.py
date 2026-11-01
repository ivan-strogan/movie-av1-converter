"""
Post-conversion verification.

Checks:
  1. Output file exists and has non-zero size.
  2. Duration is within DURATION_TOLERANCE_SECS of the source duration.
  3. Video codec of the output is 'av1'.
  4. Audio stream count >= source audio stream count.
  5. Audio stream language and title tags match source.
  6. Chapter count matches source (if source had chapters).

Returns a (ok: bool, reason: str) tuple.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

import config


def verify(input_path: Path, output_path: Path,
           source_duration: float, source_audio_count: int,
           expected_audio: Optional[list] = None) -> tuple[bool, str]:
    """
    Verify the converted output file.

    Args:
        input_path:          Original source file (used for error messages only).
        output_path:         Converted output file to verify.
        source_duration:     Duration in seconds from the original ffprobe scan.
        source_audio_count:  Number of audio streams in the original file.
        expected_audio:      Planned output tracks (audio.AudioOutputTrack). When
                             given, the output's audio is checked against the plan
                             (count, language, title) instead of the source tags,
                             since titles/languages are normalised on purpose.

    Returns:
        (True, "ok") on success, or (False, "<reason>") on failure.
    """
    # ── 1. File existence and size ─────────────────────────────────────────
    if not output_path.exists():
        return False, "Output file does not exist"

    output_size = output_path.stat().st_size
    if output_size == 0:
        return False, "Output file is empty (0 bytes)"

    # ── 2–4. Probe output ─────────────────────────────────────────────────
    probe = _ffprobe(output_path)
    if probe is None:
        return False, "ffprobe failed on output file — file may be corrupt"

    streams = probe.get("streams", [])

    # ── 3. Video codec ────────────────────────────────────────────────────
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        return False, "No video stream found in output"

    out_codec = video_streams[0].get("codec_name", "").lower()
    if out_codec != "av1":
        return False, f"Expected video codec 'av1', got '{out_codec}'"

    # ── 2. Duration check ─────────────────────────────────────────────────
    fmt = probe.get("format", {})
    out_duration = float(fmt.get("duration", 0) or 0)

    if source_duration > 0:
        delta = abs(out_duration - source_duration)
        if delta > config.DURATION_TOLERANCE_SECS:
            return False, (
                f"Duration mismatch: source={source_duration:.2f}s "
                f"output={out_duration:.2f}s delta={delta:.2f}s "
                f"(tolerance={config.DURATION_TOLERANCE_SECS}s)"
            )

    # ── 4. Audio streams ──────────────────────────────────────────────────
    out_audio = [s for s in streams if s.get("codec_type") == "audio"]
    if expected_audio is not None:
        if len(out_audio) != len(expected_audio):
            return False, (
                f"Audio stream count mismatch: expected {len(expected_audio)}, "
                f"output has {len(out_audio)}"
            )
        for i, (want, out_s) in enumerate(zip(expected_audio, out_audio)):
            out_tags = out_s.get("tags", {})
            got_lang = out_tags.get("language", "").strip()
            if got_lang == "und":
                got_lang = ""
            if want.language != got_lang:
                return False, (
                    f"Audio stream {i} language: expected '{want.language}' "
                    f"got '{got_lang}'"
                )
            got_title = out_tags.get("title", "").strip()
            if want.title != got_title:
                return False, (
                    f"Audio stream {i} title: expected '{want.title}' "
                    f"got '{got_title}'"
                )
            if want.codec_arg == "ac3":
                rate = int(out_s.get("sample_rate") or 0)
                if rate != config.AC3_SAMPLE_RATE:
                    return False, f"Audio stream {i} sample rate {rate}, expected {config.AC3_SAMPLE_RATE}"
    elif len(out_audio) < source_audio_count:
        return False, (
            f"Audio stream count dropped: source had {source_audio_count}, "
            f"output has {len(out_audio)}"
        )

    src_probe = _ffprobe(input_path)
    if src_probe is not None and expected_audio is None:
        # ── 5. Audio stream language / title tags (copy-only mode) ────────
        src_audio = [s for s in src_probe.get("streams", [])
                     if s.get("codec_type") == "audio"]
        for i, (src_s, out_s) in enumerate(zip(src_audio, out_audio)):
            src_tags = src_s.get("tags", {})
            out_tags = out_s.get("tags", {})
            for tag in ("language", "title"):
                sv = src_tags.get(tag, "").strip()
                ov = out_tags.get(tag, "").strip()
                if tag == "language":
                    if sv == "und": sv = ""
                    if ov == "und": ov = ""
                if sv and sv != ov:
                    return False, (
                        f"Audio stream {i} tag '{tag}' changed: "
                        f"source='{sv}' output='{ov}'"
                    )

    if src_probe is not None:
        # ── 6. Chapter count ──────────────────────────────────────────────
        src_chapters = src_probe.get("chapters", [])
        out_chapters = probe.get("chapters", [])
        if len(src_chapters) > 0 and len(out_chapters) < len(src_chapters):
            return False, (
                f"Chapter count dropped: source had {len(src_chapters)}, "
                f"output has {len(out_chapters)}"
            )

    return True, "ok"


def count_audio_streams(path: Path) -> int:
    """Return the number of audio streams in *path* (0 on error)."""
    probe = _ffprobe(path)
    if probe is None:
        return 0
    return sum(1 for s in probe.get("streams", [])
               if s.get("codec_type") == "audio")


# ── Internal ──────────────────────────────────────────────────────────────────

def _ffprobe(path: Path) -> Optional[dict]:
    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
