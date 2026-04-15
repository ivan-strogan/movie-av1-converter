"""
Central configuration for the AV1 batch converter.
Edit these values before running.
"""

from pathlib import Path

# ── Source and destination ────────────────────────────────────────────────────
# Candidate roots in priority order: macOS mount first, then Linux.
# The first existing directory wins.
_NAS_CANDIDATES = [
    Path("/Volumes/video"),   # macOS
    Path("/mnt/video"),       # Linux
]

def _find_nas_root() -> Path:
    for candidate in _NAS_CANDIDATES:
        if candidate.is_dir():
            return candidate
    # Fall back to the first candidate so error messages show a real path
    return _NAS_CANDIDATES[0]

_NAS_ROOT  = _find_nas_root()
MOVIES_DIR = _NAS_ROOT / "Movies"
OUTPUT_DIR = _NAS_ROOT / "Movies_AV1"

# ── Project paths ─────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent
DB_PATH      = PROJECT_DIR / "conversions.db"
LOGS_DIR     = PROJECT_DIR / "logs"
REPORTS_DIR  = PROJECT_DIR / "reports"

# ── ffmpeg / ffprobe binaries ─────────────────────────────────────────────────
FFMPEG_BIN   = "ffmpeg"
FFPROBE_BIN  = "ffprobe"

# ── SVT-AV1 encoding settings ─────────────────────────────────────────────────
VIDEO_CODEC   = "libsvtav1"
PRESET        = 5
SVTAV1_PARAMS = "tune=0"   # tune=0 → VQ mode, best for film content

# CRF per source codec — tuned so AV1 output matches source quality without
# blowing up in size.  Lower CRF = higher quality / larger file.
#
#  h264 / older codecs  → CRF 20  (less efficient originals, AV1 wins easily)
#  hevc                 → CRF 28  (already efficient; CRF 20 would make files bigger)
#  vp9                  → CRF 26  (similar efficiency to hevc)
#  fallback             → CRF 23  (safe middle ground for anything unknown)
CRF_BY_CODEC = {
    "h264":       20,
    "mpeg4":      20,
    "msmpeg4v3":  20,
    "msmpeg4v2":  20,
    "msmpeg4":    20,
    "mpeg2video": 20,
    "mpeg1video": 20,
    "wmv1":       20,
    "wmv2":       20,
    "wmv3":       20,
    "rv40":       20,
    "rv30":       20,
    "vp8":        22,
    "vp9":        26,
    "hevc":       28,
    "av1":        28,   # shouldn't happen (already AV1) but just in case
}
CRF_DEFAULT = 23   # fallback for any codec not listed above

# ── Extensions ───────────────────────────────────────────────────────────────
# Video files to convert
CONVERT_EXTENSIONS = {".mp4", ".mkv", ".avi", ".m4v", ".divx", ".rmvb"}

# DVD-structure files — skip and log
DVD_EXTENSIONS  = {".vob", ".ifo", ".bup"}

# Completely ignored (not video, no logging needed)
IGNORE_EXTENSIONS = {".vsmeta", ".db", ".jpg", ".jpeg", ".png", ".nfo",
                     ".txt", ".srt", ".idx", ".sub", ".ds_store", ""}

# ── Subtitle codec transcoding map ────────────────────────────────────────────
# Codecs that cannot be stream-copied into MKV and must be transcoded.
# Key = ffprobe codec_name, Value = ffmpeg -c:s target codec.
SUB_TRANSCODE = {
    "mov_text": "srt",   # MP4 text subs → SubRip for MKV
}
# All other subtitle codecs are stream-copied.

# ── Language tag inference from SRT filename suffixes ─────────────────────────
# e.g.  Movie.en.srt → language=eng
LANG_MAP = {
    "en": "eng", "eng": "eng",
    "ru": "rus", "rus": "rus",
    "fr": "fra", "fra": "fra",
    "de": "ger", "ger": "ger",
    "es": "spa", "spa": "spa",
    "it": "ita", "ita": "ita",
    "pt": "por", "por": "por",
    "nl": "dut", "dut": "dut",
    "pl": "pol", "pol": "pol",
    "cs": "cze", "cze": "cze",
    "sk": "slo", "slo": "slo",
    "hu": "hun", "hun": "hun",
    "ro": "rum", "rum": "rum",
    "sv": "swe", "swe": "swe",
    "no": "nor", "nor": "nor",
    "da": "dan", "dan": "dan",
    "fi": "fin", "fin": "fin",
    "tr": "tur", "tur": "tur",
    "ja": "jpn", "jpn": "jpn",
    "ko": "kor", "kor": "kor",
    "zh": "chi", "chi": "chi",
    "ar": "ara", "ara": "ara",
    "he": "heb", "heb": "heb",
    "uk": "ukr", "ukr": "ukr",
    "bg": "bul", "bul": "bul",
    "hr": "hrv", "hrv": "hrv",
    "sr": "srp", "srp": "srp",
}

# ── Verification tolerances ───────────────────────────────────────────────────
# Maximum allowed duration difference (seconds) between source and output.
DURATION_TOLERANCE_SECS = 0.5
