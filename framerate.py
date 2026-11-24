"""
Output frame rate standardisation.

Every output is 23.976 (24000/1001) or 29.97 (30000/1001) fps. A source already
at one of them is left alone; anything else is converted to the closer of the
two with ffmpeg's fps filter, which adds or drops frames. Duration and audio
sync are unchanged. (Odd rates such as 23.08 or 25 fps play badly on some TVs.)
"""

from fractions import Fraction
from typing import Optional

FILM = Fraction(24000, 1001)   # 23.976
NTSC = Fraction(30000, 1001)   # 29.97

# A source this close to a target counts as already there (timestamp rounding
# gives rates like 23.97 and 23.975).
TOLERANCE = 0.02


def parse_rate(value: str) -> float:
    """'24000/1001' or '25' -> fps as a float; 0.0 if missing or invalid."""
    try:
        num, _, den = str(value or "").partition("/")
        return float(num) / float(den) if den else float(num)
    except (ValueError, ZeroDivisionError):
        return 0.0


def source_rate(stream: dict) -> float:
    """Frame rate of an ffprobe video stream: average, else nominal."""
    return (parse_rate(stream.get("avg_frame_rate", ""))
            or parse_rate(stream.get("r_frame_rate", "")))


def target_rate(fps: float) -> Optional[str]:
    """The rate to convert to ('24000/1001'), or None to leave the video alone."""
    if fps <= 0:
        return None
    nearest = min((FILM, NTSC), key=lambda t: abs(float(t) - fps))
    if abs(float(nearest) - fps) <= TOLERANCE:
        return None
    return f"{nearest.numerator}/{nearest.denominator}"


def filter_args(target: Optional[str]) -> list[str]:
    return ["-vf", f"fps={target}"] if target else []


def describe(fps: float, target: Optional[str]) -> str:
    if not target:
        return f"{fps:.3f} fps (unchanged)"
    return f"{fps:.3f} -> {parse_rate(target):.3f} fps"
