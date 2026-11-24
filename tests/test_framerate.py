import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import converter
import framerate


class TestTargetRate(unittest.TestCase):
    FILM = "24000/1001"
    NTSC = "30000/1001"

    def test_standard_rates_left_alone(self):
        for fps in (23.976, 23.975, 23.97, 24000 / 1001, 29.97, 30000 / 1001):
            self.assertIsNone(framerate.target_rate(fps), fps)

    def test_nearest_of_the_two(self):
        cases = {
            23.077: self.FILM, 23.202: self.FILM, 23.81: self.FILM,
            24.0: self.FILM, 24.39: self.FILM, 25.0: self.FILM,
            26.9: self.FILM, 27.0: self.NTSC,
            30.0: self.NTSC, 50.0: self.NTSC, 59.94: self.NTSC, 60.0: self.NTSC,
        }
        for fps, want in cases.items():
            self.assertEqual(framerate.target_rate(fps), want, fps)

    def test_unknown_rate_left_alone(self):
        self.assertIsNone(framerate.target_rate(0))
        self.assertIsNone(framerate.target_rate(-1))


class TestParse(unittest.TestCase):
    def test_parse_rate(self):
        self.assertAlmostEqual(framerate.parse_rate("24000/1001"), 23.976, places=3)
        self.assertEqual(framerate.parse_rate("25"), 25.0)
        for bad in ("", "0/0", "abc", None):
            self.assertEqual(framerate.parse_rate(bad), 0.0)

    def test_source_rate_prefers_average(self):
        s = {"avg_frame_rate": "23077/1000", "r_frame_rate": "25/1"}
        self.assertAlmostEqual(framerate.source_rate(s), 23.077, places=3)

    def test_source_rate_falls_back_to_nominal(self):
        s = {"avg_frame_rate": "0/0", "r_frame_rate": "25/1"}
        self.assertEqual(framerate.source_rate(s), 25.0)
        self.assertEqual(framerate.source_rate({}), 0.0)


class TestCommand(unittest.TestCase):
    def build(self, fps_target):
        from pathlib import Path
        return converter._build_command(
            Path("/in.mkv"), Path("/out.mkv"), [], False, "copy",
            crf_override=30, fps_target=fps_target)

    def test_filter_added_for_odd_rate(self):
        cmd = self.build("24000/1001")
        self.assertEqual(cmd[cmd.index("-vf") + 1], "fps=24000/1001")

    def test_no_filter_when_already_standard(self):
        self.assertNotIn("-vf", self.build(None))

    def test_describe(self):
        self.assertEqual(framerate.describe(25.0, "24000/1001"), "25.000 -> 23.976 fps")
        self.assertEqual(framerate.describe(23.976, None), "23.976 fps (unchanged)")


if __name__ == "__main__":
    unittest.main()
