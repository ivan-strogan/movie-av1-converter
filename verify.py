"""
Post-conversion verification.

Checks:
  1. Output file exists and has non-zero size.
  2. Duration is within DURATION_TOLERANCE_SECS of the source duration.
  3. Video codec of the output is 'av1'.
  4. Audio stream count >= source audio stream count.

Returns a (ok: bool, reason: str) tuple.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

import config


def verify(input_path: Path, output_path: Path,
           source_duration: float, source_audio_count: int) -> tuple[bool, str]:
    """
    Verify the converted output file.

    Args:
        input_path:          Original source file (used for error messages only).
        output_path:         Converted output file to verify.
        source_duration:     Duration in seconds from the original ffprobe scan.
        source_audio_count:  Number of audio streams in the original file.

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

    # ── 4. Audio stream count ─────────────────────────────────────────────
    out_audio_count = sum(1 for s in streams if s.get("codec_type") == "audio")
    if out_audio_count < source_audio_count:
        return False, (
            f"Audio stream count dropped: source had {source_audio_count}, "
            f"output has {out_audio_count}"
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
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
