"""
Audio track planning for one conversion job.

Turns the source's audio streams into an ordered list of output tracks:

  - AC3/EAC3 not at 48 kHz  -> re-encoded to 48 kHz AC3 (44.1 kHz AC3 skips
    and loses sync on TVs after seeking)
  - AAC/MP3/etc. 5.1+       -> re-encoded to AC3 5.1 (config.AUDIO_AAC_TO_AC3)
  - everything else         -> stream copy
  - tracks detected as neither English nor Russian are dropped
    (config.AUDIO_DROP_OTHER, needs config.AUDIO_DETECT_LANG)

Every output track gets a language tag ("eng") and a standard title
("English DD 5.1") describing the OUTPUT track, and exactly one track is
flagged default: the first in the movie's own language.
"""

from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class AudioOptions:
    detect_lang:   bool = False   # run Whisper language detection per track
    drop_other:    bool = False   # drop tracks detected as not English/Russian
    aac_to_ac3:    bool = False   # re-encode AAC 5.1+ to AC3 5.1

    @classmethod
    def from_config(cls) -> "AudioOptions":
        return cls(detect_lang=config.AUDIO_DETECT_LANG,
                   drop_other=config.AUDIO_DROP_OTHER,
                   aac_to_ac3=config.AUDIO_AAC_TO_AC3)


@dataclass
class AudioStream:
    pos:         int      # position among audio streams (what --audio-lang uses)
    index:       int      # ffprobe stream index in the source file
    codec:       str
    channels:    int
    language:    str      # normalised ISO 639-2 code, "" if unknown
    sample_rate: int
    profile:     str = ""
    lang_source: str = "tag"   # "tag", "manual", "detected", "fallback", "other"

    @property
    def is_surround(self) -> bool:
        # 5+ channels covers 5.0 as well as 5.1 / 7.1
        return self.channels >= 5

    @property
    def needs_resample(self) -> bool:
        """AC3/EAC3 not at 48 kHz: copying it would keep the bad rate."""
        return (self.codec in ("ac3", "eac3")
                and self.sample_rate not in (0, config.AC3_SAMPLE_RATE))

    @property
    def is_aac_surround(self) -> bool:
        return self.codec in config.AAC_TRANSCODE_CODECS and self.is_surround


@dataclass
class AudioOutputTrack:
    source_index: int
    codec_arg:    str     # "copy" or "ac3"
    bitrate_arg:  str     # "" for copy
    out_channels: int
    title:        str
    language:     str
    is_default:   bool = False


# ── Parsing ───────────────────────────────────────────────────────────────────

def normalize_lang(tag: str) -> str:
    """'en' -> 'eng'; 'und' / '' -> ''; unknown codes pass through lower-cased."""
    t = (tag or "").strip().lower()
    if t in ("", "und", "undefined"):
        return ""
    return config.LANG_MAP.get(t, t)


def parse_streams(streams: list[dict]) -> list[AudioStream]:
    """Build AudioStream objects from ffprobe stream dicts (audio only)."""
    result = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        result.append(AudioStream(
            pos         = len(result),
            index       = s["index"],
            codec       = (s.get("codec_name") or "").lower(),
            channels    = int(s.get("channels") or 0),
            language    = normalize_lang(s.get("tags", {}).get("language", "")),
            sample_rate = int(s.get("sample_rate") or 0),
            profile     = s.get("profile", "") or "",
        ))
    return result


def parse_lang_assignments(audio_lang: str, unknown_pos: list[int]) -> dict[int, str]:
    """
    Parse --audio-lang into {audio_position: lang_code}.

      'eng'          -> 'eng' for every track with no language
      '0:eng'        -> 'eng' for audio track 0 only
      '0:eng,1:fra'  -> explicit per-track mapping
    """
    if ":" not in audio_lang:
        return {pos: normalize_lang(audio_lang) for pos in unknown_pos}

    mapping: dict[int, str] = {}
    for part in audio_lang.split(","):
        part = part.strip()
        if ":" in part:
            idx_str, lang = part.split(":", 1)
            mapping[int(idx_str.strip())] = normalize_lang(lang)
    return mapping


def apply_manual_languages(audio: list[AudioStream], audio_lang: str) -> None:
    """Apply --audio-lang in place. Overrides detection and source tags."""
    if not audio_lang:
        return
    unknown = [a.pos for a in audio if not a.language]
    by_pos = {a.pos: a for a in audio}
    for pos, lang in parse_lang_assignments(audio_lang, unknown).items():
        if pos in by_pos and lang:
            by_pos[pos].language = lang
            by_pos[pos].lang_source = "manual"


def unverified(audio: list[AudioStream]) -> list[int]:
    """Positions whose language is still unknown or only a guess."""
    return [a.pos for a in audio if not a.language or a.lang_source == "fallback"]


# ── Titles ────────────────────────────────────────────────────────────────────

_FORMAT_NAMES = {
    "ac3": "DD", "eac3": "DD+", "dts": "DTS", "dca": "DTS",
    "truehd": "TrueHD", "mlp": "TrueHD", "aac": "AAC", "mp3": "MP3",
    "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC",
}

_CHANNEL_LABELS = {1: "1.0", 2: "2.0", 3: "2.1", 4: "4.0", 5: "5.0",
                   6: "5.1", 7: "6.1", 8: "7.1"}


def movie_language(title: str) -> str:
    """Language a movie is in, guessed from its title: Cyrillic -> 'rus', else 'eng'."""
    return "rus" if any("Ѐ" <= ch <= "ӿ" for ch in title) else "eng"


def format_label(codec: str, profile: str = "") -> str:
    codec = (codec or "").lower()
    p = (profile or "").lower()
    name = _FORMAT_NAMES.get(codec, codec.upper())
    if codec in ("dts", "dca"):
        words = p.replace("-", " ").split()
        if "dts:x" in p:
            name = "DTS:X"
        elif "ma" in words:
            name = "DTS-HD MA"
        elif "hra" in words:
            name = "DTS-HD HRA"
    if "atmos" in p:
        name += " Atmos"
    return name


def track_title(language: str, codec: str, channels: int, profile: str = "") -> str:
    """Standard audio title, e.g. 'English DD 5.1'."""
    lang = config.LANG_NAMES.get(language, language.upper() if language else "Unknown")
    chan = _CHANNEL_LABELS.get(channels, f"{channels}ch")
    return f"{lang} {format_label(codec, profile)} {chan}"


# ── Planning ──────────────────────────────────────────────────────────────────

def plan_tracks(audio: list[AudioStream], movie_title: str,
                opts: Optional[AudioOptions] = None) -> list[AudioOutputTrack]:
    """Return the ordered output tracks for a file's audio streams."""
    opts = opts or AudioOptions()

    keep = audio
    if opts.drop_other:
        kept = [a for a in audio if a.lang_source != "other"]
        keep = kept or audio   # never drop every track

    tracks = []
    for a in keep:
        if a.needs_resample or (opts.aac_to_ac3 and a.is_aac_surround):
            out_ch = min(a.channels, 6)   # AC3 tops out at 5.1: 6.1/7.1 downmix
            tracks.append(AudioOutputTrack(
                source_index = a.index,
                codec_arg    = "ac3",
                bitrate_arg  = (config.AC3_BITRATE if a.is_surround
                                else config.AC3_STEREO_BITRATE),
                out_channels = out_ch,
                title        = track_title(a.language, "ac3", out_ch),
                language     = a.language,
            ))
        else:
            tracks.append(AudioOutputTrack(
                source_index = a.index,
                codec_arg    = "copy",
                bitrate_arg  = "",
                out_channels = a.channels,
                title        = track_title(a.language, a.codec, a.channels, a.profile),
                language     = a.language,
            ))

    mark_default(tracks, movie_title)
    return tracks


def mark_default(tracks: list[AudioOutputTrack], movie_title: str) -> None:
    """Flag exactly one track default: the first in the movie's own language,
    or the first track if none is."""
    lang = movie_language(movie_title)
    chosen = next((i for i, t in enumerate(tracks) if t.language == lang), 0)
    for i, t in enumerate(tracks):
        t.is_default = i == chosen


# ── ffmpeg arguments ──────────────────────────────────────────────────────────

def map_args(tracks: list[AudioOutputTrack]) -> list[str]:
    args: list[str] = []
    for t in tracks:
        args += ["-map", f"0:{t.source_index}"]
    return args


def codec_args(tracks: list[AudioOutputTrack]) -> list[str]:
    args: list[str] = []
    for i, t in enumerate(tracks):
        args += [f"-c:a:{i}", t.codec_arg]
        if t.codec_arg == "ac3":
            args += [f"-b:a:{i}", t.bitrate_arg,
                     f"-ar:a:{i}", str(config.AC3_SAMPLE_RATE),
                     f"-ac:a:{i}", str(t.out_channels)]
        if t.language:
            args += [f"-metadata:s:a:{i}", f"language={t.language}"]
        args += [f"-metadata:s:a:{i}", f"title={t.title}",
                 f"-disposition:a:{i}", "default" if t.is_default else "0"]
    return args


def describe(tracks: list[AudioOutputTrack]) -> str:
    return ", ".join(
        f"{t.title} ({'re-encode' if t.codec_arg == 'ac3' else 'copy'})"
        for t in tracks
    )
