import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import verify


def stream(kind, duration_tag=None, duration=None):
    s = {"codec_type": kind, "tags": {}}
    if duration_tag:
        s["tags"]["DURATION"] = duration_tag
    if duration is not None:
        s["duration"] = str(duration)
    return s


class TestContentDuration(unittest.TestCase):
    def test_subtitle_running_past_the_end_is_ignored(self):
        probe = {
            "streams": [
                stream("video", "02:15:11.000000000"),
                stream("audio", "02:15:11.200000000"),
                stream("subtitle", "02:16:23.883000000", duration=8183.88),
            ],
            "format": {"duration": "8183.88"},
        }
        self.assertAlmostEqual(verify.content_duration(probe), 8111.2, places=3)

    def test_uses_longest_of_video_and_audio(self):
        probe = {"streams": [stream("video", "00:01:00.000000000"),
                             stream("audio", "00:01:00.500000000")],
                 "format": {"duration": "999"}}
        self.assertAlmostEqual(verify.content_duration(probe), 60.5, places=3)

    def test_falls_back_to_stream_duration(self):
        probe = {"streams": [stream("video", duration=42.0)], "format": {"duration": "999"}}
        self.assertEqual(verify.content_duration(probe), 42.0)

    def test_falls_back_to_container_when_no_tags(self):
        probe = {"streams": [stream("video"), stream("audio")],
                 "format": {"duration": "3789.658"}}
        self.assertEqual(verify.content_duration(probe), 3789.658)

    def test_bad_tag_falls_back(self):
        probe = {"streams": [stream("video", "garbage")], "format": {"duration": "12.5"}}
        self.assertEqual(verify.content_duration(probe), 12.5)

    def test_no_streams(self):
        self.assertEqual(verify.content_duration({}), 0.0)

    def test_tag_parsing(self):
        self.assertAlmostEqual(verify._tag_seconds("01:03:09.440000000"), 3789.44, places=3)
        self.assertEqual(verify._tag_seconds(""), 0.0)
        self.assertEqual(verify._tag_seconds(None), 0.0)


if __name__ == "__main__":
    unittest.main()
