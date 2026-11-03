import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import db
import main


class TestRenameSourceFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.movies = root / "Movies"
        self.movies.mkdir()
        self._saved = (config.DB_PATH, config.MOVIES_DIR)
        config.DB_PATH = root / "test.db"
        config.MOVIES_DIR = self.movies
        db.init_db()

    def tearDown(self):
        config.DB_PATH, config.MOVIES_DIR = self._saved
        self.tmp.cleanup()

    def make_folder(self, name, files):
        folder = self.movies / name
        folder.mkdir()
        paths = []
        for fname, status in files:
            p = folder / fname
            p.write_bytes(b"x")
            db.upsert_pending(p, Path("/out") / fname, "h264", 1, 60.0)
            if status == "done":
                db.mark_done(p, 1)
            elif status == "failed":
                db.mark_failed(p, "boom")
            elif status == "in_progress":
                db.mark_in_progress(p)
            elif status == "skipped":
                db.mark_skipped(p, "dvd")
            paths.append(p)
        return folder, paths

    def paths_in_db(self):
        conn = db._connect()
        try:
            return sorted(r["input_path"] for r in
                          conn.execute("SELECT input_path FROM conversions"))
        finally:
            conn.close()

    def test_not_renamed_while_other_files_pending(self):
        folder, (a, b) = self.make_folder("Show (1972)", [("1.mp4", "done"), ("2.mp4", "pending")])
        self.assertIsNone(main._rename_source_folder(a))
        self.assertTrue(folder.exists())
        self.assertEqual(self.paths_in_db(), sorted([str(a), str(b)]))

    def test_failed_and_in_progress_also_block(self):
        for i, status in enumerate(("failed", "in_progress")):
            folder, (a, _) = self.make_folder(f"Show {i}", [("1.mp4", "done"), ("2.mp4", status)])
            self.assertIsNone(main._rename_source_folder(a))
            self.assertTrue(folder.exists())

    def test_renamed_when_all_done_and_every_row_updated(self):
        folder, (a, b) = self.make_folder("Show (1972)", [("1.mp4", "done"), ("2.mp4", "done")])
        new_a = main._rename_source_folder(b)
        new_folder = self.movies / ("Show (1972)" + config.SOURCE_DONE_SUFFIX)
        self.assertEqual(new_a, new_folder / "2.mp4")
        self.assertFalse(folder.exists())
        self.assertTrue((new_folder / "1.mp4").exists())
        self.assertEqual(self.paths_in_db(),
                         sorted([str(new_folder / "1.mp4"), str(new_folder / "2.mp4")]))

    def test_skipped_files_do_not_block(self):
        folder, (a, _) = self.make_folder("Disc", [("movie.mp4", "done"), ("menu.vob", "skipped")])
        self.assertIsNotNone(main._rename_source_folder(a))
        self.assertFalse(folder.exists())

    def test_sibling_folder_with_same_prefix_is_not_affected(self):
        _, (a,) = self.make_folder("Movie 1", [("a.mp4", "done")])
        _, (other,) = self.make_folder("Movie 10", [("b.mp4", "pending")])
        main._rename_source_folder(a)
        self.assertIn(str(other), self.paths_in_db())
        self.assertTrue(other.exists())

    def test_special_characters_in_folder_name(self):
        folder, (a, b) = self.make_folder("100%_Sure [480p] Ё", [("1.mp4", "done"), ("2.mp4", "done")])
        self.assertIsNotNone(main._rename_source_folder(a))
        new_folder = self.movies / ("100%_Sure [480p] Ё" + config.SOURCE_DONE_SUFFIX)
        self.assertEqual(self.paths_in_db(),
                         sorted([str(new_folder / "1.mp4"), str(new_folder / "2.mp4")]))

    def test_flat_file_is_skipped(self):
        p = self.movies / "flat.mp4"
        p.write_bytes(b"x")
        db.upsert_pending(p, Path("/out/flat.mkv"), "h264", 1, 60.0)
        db.mark_done(p, 1)
        self.assertIsNone(main._rename_source_folder(p))


if __name__ == "__main__":
    unittest.main()
