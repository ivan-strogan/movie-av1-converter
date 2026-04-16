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

# ── CRF selection based on source bitrate + codec ────────────────────────────
#
# Goal: AV1 output should always be smaller than the source while matching
# its visual quality.  A low-bitrate source (e.g. a 700 kbps YIFY encode)
# doesn't need a high-quality AV1 target — it just needs to match what's there.
#
# Bitrate tiers (kbps) → base CRF for h264-class sources:
#   < 500    → 38   (very compressed, tiny source)
#   500-1000 → 34
#   1000-2000→ 30
#   2000-4000→ 26
#   4000-8000→ 22
#   > 8000   → 20   (high-quality source, preserve faithfully)
#
# Codec efficiency adjustments applied on top of the bitrate tier:
#   hevc / av1  → +6  (already very efficient)
#   vp9         → +4
#   mpeg4/older → -2  (less efficient, can afford lower CRF)
CRF_BITRATE_TIERS = [
    (500,  38),
    (1000, 34),
    (2000, 30),
    (4000, 26),
    (8000, 22),
]
CRF_BITRATE_MAX = 20   # used when bitrate > last tier

# Added to base CRF depending on codec family
CRF_CODEC_OFFSET = {
    "hevc":       +6,
    "av1":        +6,
    "vp9":        +4,
    "vp8":        +2,
    "mpeg4":      -2,
    "msmpeg4v3":  -2,
    "msmpeg4v2":  -2,
    "msmpeg4":    -2,
    "mpeg2video": -2,
    "mpeg1video": -2,
    "wmv1":       -2,
    "wmv2":       -2,
    "wmv3":       -2,
}

CRF_MIN = 18
CRF_MAX = 51

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
