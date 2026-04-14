"""
Central configuration for the AV1 batch converter.
Edit these values before running.
"""

from pathlib import Path

# ── Source and destination ────────────────────────────────────────────────────
MOVIES_DIR   = Path("/Volumes/video/Movies")
OUTPUT_DIR   = Path("/Volumes/video/Movies_AV1")

# ── Project paths ─────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent
DB_PATH      = PROJECT_DIR / "conversions.db"
LOGS_DIR     = PROJECT_DIR / "logs"
REPORTS_DIR  = PROJECT_DIR / "reports"

# ── ffmpeg / ffprobe binaries ─────────────────────────────────────────────────
FFMPEG_BIN   = "ffmpeg"
FFPROBE_BIN  = "ffprobe"

# ── SVT-AV1 encoding settings ─────────────────────────────────────────────────
# CRF 20 with preset 5 is considered perceptually transparent (visually lossless)
# relative to typical H.264 / MPEG-4 sources, while still yielding ~30-50%
# smaller files than the H.264 source at equivalent quality.
VIDEO_CODEC      = "libsvtav1"
CRF              = 20
PRESET           = 5
SVTAV1_PARAMS    = "tune=0"   # tune=0 → VQ mode, best for film content

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
