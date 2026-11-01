import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import audio
import langdetect


def stream(index, codec, channels, rate, lang="", profile=""):
    s = {"index": index, "codec_type": "audio", "codec_name": codec,
         "channels": channels, "sample_rate": str(rate), "profile": profile}
    if lang:
        s["tags"] = {"language": lang}
    return s


def plan(streams, title="Movie (2000)", **opts):
    tracks = audio.parse_streams(streams)
    return audio.plan_tracks(tracks, title, audio.AudioOptions(**opts))


def arg_after(args, flag):
    return args[args.index(flag) + 1]


class TestPlan(unittest.TestCase):
    def test_441_ac3_is_resampled_to_48k(self):
        t = plan([stream(1, "ac3", 6, 44100, "eng")])[0]
        self.assertEqual((t.codec_arg, t.bitrate_arg), ("ac3", "640k"))
        self.assertEqual(arg_after(audio.codec_args([t]), "-ar:a:0"), "48000")

    def test_48k_ac3_is_copied(self):
        t = plan([stream(1, "ac3", 6, 48000, "eng")])[0]
        self.assertEqual(t.codec_arg, "copy")
        self.assertEqual(t.title, "English DD 5.1")

    def test_441_stereo_eac3_uses_stereo_bitrate(self):
        t = plan([stream(1, "eac3", 2, 44100, "eng")])[0]
        self.assertEqual((t.codec_arg, t.bitrate_arg), ("ac3", "192k"))

    def test_71_resample_downmixes_to_51(self):
        t = plan([stream(1, "eac3", 8, 44100, "eng")])[0]
        self.assertEqual(t.out_channels, 6)
        self.assertEqual(t.title, "English DD 5.1")

    def test_aac_51_copied_by_default_encoded_with_flag(self):
        src = [stream(1, "aac", 6, 48000, "eng")]
        self.assertEqual(plan(src)[0].codec_arg, "copy")
        self.assertEqual(plan(src, aac_to_ac3=True)[0].codec_arg, "ac3")

    def test_dts_and_truehd_always_copied_even_at_441(self):
        for codec in ("dts", "truehd"):
            self.assertEqual(plan([stream(1, codec, 6, 44100, "eng")])[0].codec_arg, "copy")

    def test_language_codes_normalised(self):
        self.assertEqual(audio.normalize_lang("en"), "eng")
        self.assertEqual(audio.normalize_lang("und"), "")
        self.assertEqual(audio.normalize_lang(""), "")
        self.assertEqual(plan([stream(1, "aac", 2, 48000, "en")])[0].language, "eng")

    def test_titles(self):
        self.assertEqual(audio.track_title("rus", "ac3", 6), "Russian DD 5.1")
        self.assertEqual(audio.track_title("eng", "dts", 8, "DTS-HD MA"), "English DTS-HD MA 7.1")
        self.assertEqual(audio.track_title("eng", "truehd", 8, "Dolby TrueHD + Dolby Atmos"),
                         "English TrueHD Atmos 7.1")
        self.assertEqual(audio.track_title("", "aac", 2), "Unknown AAC 2.0")

    def test_one_default_in_movie_language(self):
        src = [stream(1, "aac", 2, 48000, "rus"), stream(2, "aac", 2, 48000, "eng")]
        tracks = plan(src, "Some Movie (2000)")
        self.assertEqual([t.is_default for t in tracks], [False, True])
        tracks = plan(src, "Ирония судьбы (1976)")
        self.assertEqual([t.is_default for t in tracks], [True, False])

    def test_default_falls_back_to_first(self):
        tracks = plan([stream(1, "aac", 2, 48000, "fra"), stream(2, "aac", 2, 48000, "ita")])
        self.assertEqual([t.is_default for t in tracks], [True, False])

    def test_drop_other(self):
        tracks = audio.parse_streams([stream(1, "ac3", 6, 48000, "eng"),
                                      stream(2, "ac3", 6, 48000, "fra")])
        tracks[1].lang_source = "other"
        out = audio.plan_tracks(tracks, "Movie", audio.AudioOptions(drop_other=True))
        self.assertEqual([t.source_index for t in out], [1])
        out = audio.plan_tracks(tracks, "Movie", audio.AudioOptions())
        self.assertEqual(len(out), 2)

    def test_drop_other_never_drops_everything(self):
        tracks = audio.parse_streams([stream(1, "ac3", 6, 48000, "fra")])
        tracks[0].lang_source = "other"
        out = audio.plan_tracks(tracks, "Movie", audio.AudioOptions(drop_other=True))
        self.assertEqual(len(out), 1)


class TestManualLanguage(unittest.TestCase):
    def test_plain_code_only_fills_unknown(self):
        tracks = audio.parse_streams([stream(1, "ac3", 6, 48000, "fra"),
                                      stream(2, "ac3", 6, 48000)])
        audio.apply_manual_languages(tracks, "eng")
        self.assertEqual([t.language for t in tracks], ["fra", "eng"])

    def test_per_stream(self):
        tracks = audio.parse_streams([stream(1, "ac3", 6, 48000), stream(2, "ac3", 6, 48000)])
        audio.apply_manual_languages(tracks, "0:eng,1:fr")
        self.assertEqual([t.language for t in tracks], ["eng", "fra"])

    def test_unverified(self):
        tracks = audio.parse_streams([stream(1, "ac3", 6, 48000, "eng"), stream(2, "ac3", 6, 48000)])
        self.assertEqual(audio.unverified(tracks), [1])


class TestDecide(unittest.TestCase):
    def test_english(self):
        self.assertEqual(langdetect.decide([(0.95, 0.02)] * 4).language, "eng")

    def test_russian(self):
        self.assertEqual(langdetect.decide([(0.02, 0.95)] * 4).language, "rus")

    def test_other_language(self):
        self.assertEqual(langdetect.decide([(0.05, 0.05)] * 4).language, "other")

    def test_inconclusive_when_few_clips(self):
        self.assertEqual(langdetect.decide([(0.95, 0.02)] * 2).language, "")

    def test_disagreement(self):
        clips = [(0.95, 0.02)] * 3 + [(0.02, 0.95)]
        self.assertEqual(langdetect.decide(clips).language, "")


if __name__ == "__main__":
    unittest.main()
