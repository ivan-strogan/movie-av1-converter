# movie-av1-converter

Batch converts a movie library to AV1 (MKV container) using ffmpeg and SVT-AV1.
Preserves audio streams (with 48 kHz, language and title normalisation), embedded subtitles, external SRT files, chapters, and metadata.
Originals are never modified.

---

## What it does

- Walks a source directory and queues every video file into a SQLite database
- Encodes each file to AV1 using `libsvtav1` inside an MKV container
- Before the full encode, runs a probe pass on two 5-minute samples (at 25% and 75% through the film) to find a CRF value that guarantees the output is at least 10% smaller than the source
- If the probe sample is too large, CRF is raised by 4 and the sample is re-encoded, up to 4 attempts
- After encoding, verifies the output: correct codec, matching duration, and no lost audio streams
- Tracks progress in SQLite so any interrupted run resumes from where it left off
- Mirrors the source folder structure under a separate output directory

---

## Requirements

- Python 3.10+
- ffmpeg with `libsvtav1` support (ffmpeg 6+ on most platforms)

### Quick setup

```bash
bash setup.sh
```

The setup script detects macOS (Homebrew) or Linux (apt) and installs ffmpeg and Python. On Linux, if the system ffmpeg does not include `libsvtav1`, it adds the `ubuntuhandbook1/ffmpeg7` PPA automatically.

---

## Configuration

All settings are in `config.py`. The most important ones:

| Setting | Default | Description |
|---|---|---|
| `MOVIES_DIR` | `/Volumes/video/Movies` | Source directory (auto-detects macOS/Linux mount) |
| `OUTPUT_DIR` | `/Volumes/video/Movies_AV1` | Output directory |
| `PRESET` | `5` | SVT-AV1 preset (0=slowest/best, 13=fastest) |
| `PROBE_TARGET_RATIO` | `0.9` | Sample must be at least this much smaller than source (0.9 = 10% smaller) |
| `CRF_MIN` | `18` | Minimum CRF allowed |
| `CRF_MAX` | `51` | Maximum CRF allowed |

### NAS / mount point detection

The converter tries `/Volumes/video` (macOS) then `/mnt/video` (Linux). Edit `_NAS_CANDIDATES` in `config.py` to change these.

### CRF selection

The starting CRF is picked from the source file's estimated bitrate, then adjusted for codec efficiency:

| Source bitrate | Base CRF |
|---|---|
| Under 500 kbps | 38 |
| 500 - 1000 kbps | 34 |
| 1000 - 2000 kbps | 30 |
| 2000 - 4000 kbps | 26 |
| 4000 - 8000 kbps | 22 |
| Over 8000 kbps | 20 |

Codec offsets are then applied on top (e.g. HEVC/AV1 sources get +6 since they are already efficient; older codecs like MPEG-4 get -2).

The probe pass can only raise the CRF from this starting value, never lower it.

---

## Usage

### 1. Scan

Walks the source directory and populates the database. Safe to run multiple times -- existing entries are not overwritten.

```bash
python3 main.py scan
```

Preview what would be queued without writing anything:

```bash
python3 main.py scan --dry-run
```

### 2. Convert

Process all pending jobs:

```bash
python3 main.py convert
```

Convert only the first N files:

```bash
python3 main.py convert --limit 5
```

Convert a specific file by name (case-insensitive partial match):

```bash
python3 main.py convert --file "goofy movie"
```

Force re-encode a file even if it was already converted:

```bash
python3 main.py convert --reconvert "bug's life"
```

Set an audio language by hand (see [Audio handling](#audio-handling)):

```bash
python3 main.py convert --file "name" --audio-lang eng
```

Retry all previously failed jobs:

```bash
python3 main.py convert --retry-failed
```

Preview the ffmpeg commands without running them:

```bash
python3 main.py convert --dry-run
```

### 3. Status

Show a summary of how many files are in each state:

```bash
python3 main.py status
```

Example output:

```
Status           Count      %
------------------------------
pending            621   97.2%
in_progress          0    0.0%
done                15    2.3%
failed               3    0.5%
skipped             12    1.9%
------------------------------
TOTAL              651

Space saved (done jobs): 4.21 GB  (avg compression ratio 0.58)
```

### 4. Report

Write text reports to the `reports/` directory:

```bash
python3 main.py report
```

Generates:
- `reports/skipped_files.txt` -- files skipped during scan with reasons
- `reports/failed_files.txt` -- files that failed conversion with error messages
- `reports/completion_summary.txt` -- overall stats

---

## What the conversion output looks like

```
[1/1] A Bug's Life (1998) [720p]/A Bug's Life (1998) [720p].mkv
  Source: h264  1280x544  886 kbps  602 MB  94min  starting CRF=34
  Probe: extracting samples  (clip A at ~23min, clip B at ~71min, 5min each)
  Probe: samples ready  (2 x 5min)
  Probe: attempt 1/4  CRF=34
  Probe: encoding clip A  CRF=34
  [████████████████████████████] 100.0%  ETA 00m00s  3.1x  27 fps
  Probe: encoding clip B  CRF=34
  [████████████████████████████] 100.0%  ETA 00m00s  3.2x  28 fps
  Probe: sample too large  ratio=1.18  (71MB vs ~60MB source)  need <90%  ->  raising CRF 34 -> 38
  Probe: attempt 2/4  CRF=38
  Probe: encoding clip A  CRF=38
  [████████████████████████████] 100.0%  ETA 00m00s  3.8x  34 fps
  Probe: encoding clip B  CRF=38
  [████████████████████████████] 100.0%  ETA 00m00s  3.7x  33 fps
  Probe: sample OK  ratio=0.81  (49MB vs ~60MB source)  CRF=38 accepted
  Encoding full movie  CRF=38
  [████████████████░░░░░░░░░░░░]  58.4%  ETA 12m10s  2.9x  26 fps
```

---

## Audio handling

Every output audio track gets a language tag (`eng`, `rus`, ...) and a standard
title (`English DD 5.1`) describing the **output** track, and exactly one track
is flagged default: the first in the movie's own language (Cyrillic title =
Russian, otherwise English), or the first track if none matches.

| Source track | Action |
|---|---|
| AC3 / EAC3 at 48 kHz | Stream copy |
| AC3 / EAC3 **not** at 48 kHz | Re-encode to 48 kHz AC3 (44.1 kHz AC3 skips and loses sync on TVs after seeking) |
| DTS / DTS-HD / TrueHD / stereo or mono AAC | Stream copy |
| AAC / MP3 5.1+ | Re-encode to AC3 5.1 |

AC3 tops out at 5.1, so 6.1/7.1 sources are downmixed when re-encoded. Surround
AC3 is encoded at 640 kbps, stereo at 192 kbps.

Languages are resolved in this order: `--audio-lang`, then Whisper detection,
then the source tag (`en` is normalised to `eng`; `und` means unknown). Tracks
whose language is unknown or only a guess are flagged in `python3 main.py status`.

**Whisper detection** ignores source tags: it samples speech clips across the
film and detects English vs Russian (Ukrainian counts as Russian). Tracks that are
mostly neither English nor Russian are dropped (never all of them). It is slower
and needs `pip install faster-whisper numpy`; without them a warning is printed
and the source tags are kept. If detection is inconclusive the source tag is
kept, or the title guess is used and the track is flagged.

The three behaviours (detect, drop other languages, AAC to AC3) are switches in
`config.py` (`AUDIO_DETECT_LANG`, `AUDIO_DROP_OTHER`, `AUDIO_AAC_TO_AC3`), all on
by default. Nothing needs to be passed on the command line.

Run the tests with `python3 -m unittest tests.test_audio`.

---

## Subtitle handling

| Source codec | Output codec | Notes |
|---|---|---|
| `subrip` (SRT) | copy | Pass through |
| `ass` / `ssa` | copy | Pass through |
| `hdmv_pgs_subtitle` | copy | Blu-ray bitmaps, pass through |
| `dvd_subtitle` | copy | DVD bitmaps, pass through |
| `mov_text` | `srt` | MP4 text subs transcoded for MKV compatibility |

External SRT files are auto-detected by filename. If a file named `Movie.mkv` has `Movie.en.srt` and `Movie.ru.srt` in the same folder, both are embedded into the output with the correct language tags.

---

## Skipped files

These are logged but never converted:

- DVD files (`.vob`, `.ifo`, `.bup`) -- require manual handling
- Files that are already AV1
- Files where ffprobe fails (corrupt or unsupported format)
- Files whose output already exists in the destination

Non-video files (`.srt`, `.nfo`, `.jpg`, etc.) are silently ignored.

---

## Resuming after interruption

Any file left in `in_progress` state (due to a crash or Ctrl+C) is automatically reset to `pending` the next time `convert` is run.

---

## Running in the background (macOS)

To prevent the Mac from sleeping during a long conversion:

```bash
caffeinate -i python3 main.py convert
```

---

## Project structure

```
main.py          -- CLI entry point (scan, convert, status, report)
scanner.py       -- walks source directory and populates the database
converter.py     -- ffmpeg command builder, probe pass, and full encode
audio.py         -- audio track planning: 48 kHz rule, titles, languages, default track
langdetect.py    -- optional Whisper audio language detection
tests/           -- unit tests for audio planning
verify.py        -- post-conversion output verification
db.py            -- SQLite interface
config.py        -- all settings and constants
setup.sh         -- dependency installer (macOS and Linux)
conversions.db   -- job queue and progress tracker (gitignored)
logs/            -- per-file ffmpeg output (gitignored)
reports/         -- generated text reports
```
