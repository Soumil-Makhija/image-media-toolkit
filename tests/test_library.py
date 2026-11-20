import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from imagelens.library import Library, LibraryError


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "library")

    def image(self, filename="sample.png", format="PNG", color="red"):
        target = self.root / filename
        with Image.new("RGB", (12, 8), color) as image:
            image.save(target, format=format)
        return target

    def database(self, path=None):
        connection = sqlite3.connect(path or self.library.database)
        self.addCleanup(connection.close)
        return connection

    def test_import_preserves_bytes_hash_dimensions_and_actual_format(self):
        path = self.image("misleading.txt", "JPEG")
        raw = path.read_bytes()
        record = self.library.add(path)
        self.assertEqual(record["id"], hashlib.sha256(raw).hexdigest())
        self.assertEqual((record["mime"], record["width"], record["height"]), ("image/jpeg", 12, 8))
        self.assertTrue(record["object_path"].endswith(".jpg"))
        self.assertEqual((record["alt_text"], record["tags"], record["review_status"]), ("", [], "needs_review"))
        self.assertEqual(self.library.read_original(record["id"]), raw)
        self.assertEqual(path.read_bytes(), raw)

    def test_png_jpeg_and_webp(self):
        for format in ("PNG", "JPEG", "WEBP"):
            with self.subTest(format=format):
                record = self.library.add(self.image(format + ".data", format))
                self.assertEqual(record["mime"], "image/" + format.lower())

    def test_dedup_preserves_first_filename_edits_and_creation_date(self):
        original = self.image("first.png")
        record = self.library.add(original)
        edited = self.library.edit(record["id"], "A red square", ["Red", "red", " graphic "])
        duplicate = self.root / "second.png"
        duplicate.write_bytes(original.read_bytes())
        self.assertEqual(self.library.add(duplicate), edited)
        self.assertEqual(edited["tags"], ["Red", "graphic"])
        self.assertEqual(edited["original_filename"], "first.png")
        self.assertEqual(edited["created_at"], record["created_at"])
        self.assertEqual(len(self.library.list()), 1)

    def test_persistence_vision_draft_and_manual_review(self):
        record = self.library.add(self.image())
        draft = self.library.edit(record["id"], "A red rectangle", ["red"], source="vision", reviewed=False)
        self.assertEqual((draft["metadata_source"], draft["review_status"]), ("vision", "needs_review"))
        reopened = Library(self.library.directory)
        self.assertEqual(reopened.get(record["id"]), draft)
        reviewed = reopened.edit(record["id"], "A plain red rectangle", ["plain"])
        self.assertEqual((reviewed["metadata_source"], reviewed["review_status"]), ("manual", "reviewed"))

    def test_expected_metadata_guard_applies_current_draft_but_rejects_stale_draft(self):
        path = self.image()
        original = path.read_bytes()
        record = self.library.add(path)
        fields = {"alt_text", "tags", "metadata_source", "review_status"}
        baseline = {field: record[field] for field in fields}
        draft = self.library.edit(record["id"], "A red rectangle", ["red"], source="vision",
                                  reviewed=False, expected_metadata=baseline)
        self.assertEqual(draft["alt_text"], "A red rectangle")
        reviewed = self.library.edit(record["id"], "Manually reviewed red rectangle", ["reviewed"])
        with self.assertRaisesRegex(LibraryError, "Stale metadata"):
            self.library.edit(record["id"], "Old vision text", ["old"], source="vision",
                              reviewed=False, expected_metadata=baseline)
        self.assertEqual(self.library.get(record["id"]), reviewed)
        self.assertEqual(self.library.read_original(record["id"]), original)
        self.assertEqual(path.read_bytes(), original)

    def test_expected_metadata_guard_rejects_malformed_or_noncanonical_baselines(self):
        record = self.library.add(self.image())
        baseline = {key: record[key] for key in ("alt_text", "tags", "metadata_source", "review_status")}
        candidates = [False, {}, {**baseline, "extra": True}, {**baseline, "tags": "tag"},
                      {**baseline, "tags": [" red "]}, {**baseline, "tags": ["Red", "red"]},
                      {**baseline, "review_status": True}, {**baseline, "metadata_source": []}]
        for expected in candidates:
            with self.subTest(expected=expected), self.assertRaises(LibraryError):
                self.library.edit(record["id"], "New text", [], expected_metadata=expected)
        self.assertEqual(self.library.get(record["id"]), record)

    def test_literal_casefold_and_search_across_filename_alt_tags(self):
        red = self.library.add(self.image("special%.png"))
        blue = self.library.add(self.image("blue.png", color="blue"))
        self.library.edit(red["id"], "A RED rectangle", ["Straße", "50%"])
        self.library.edit(blue["id"], "A blue rectangle", ["shape"])
        self.assertEqual([r["id"] for r in self.library.search("red STRASSE")], [red["id"]])
        self.assertEqual([r["id"] for r in self.library.search("special%")], [red["id"]])
        self.assertEqual(self.library.search("' OR 1=1 --"), [])
        self.assertEqual(len(self.library.search("rectangle")), 2)

    def test_metadata_limits_types_and_sql_are_not_executed(self):
        record = self.library.add(self.image())
        invalid = [("x" * 1001, []), (True, []), ("x", "tag"), ("x", [""]),
                   ("x", ["x" * 51]), ("x", [str(i) for i in range(21)]),
                   ("x\x1b", []), ("x", [True]), ("x", ["a\nb"])]
        for alt, tags in invalid:
            with self.subTest(alt=repr(alt)[:30], tags=tags), self.assertRaises(LibraryError):
                self.library.edit(record["id"], alt, tags)
        malicious = "'); DROP TABLE images; --"
        self.assertEqual(self.library.edit(record["id"], malicious, [malicious])["alt_text"], malicious)
        self.assertEqual(len(self.library.list()), 1)
        for source, reviewed in (([], True), ("other", True), ("manual", 1)):
            with self.subTest(source=source, reviewed=reviewed), self.assertRaises(LibraryError):
                self.library.edit(record["id"], "alt", [], source=source, reviewed=reviewed)

    def test_unknown_ids_and_search_limits(self):
        for image_id in ("missing", "../file", "A" * 64, True, "0" * 64):
            with self.subTest(image_id=image_id), self.assertRaises(LibraryError):
                self.library.get(image_id)
        for query in ("", " " * 3, "x" * 201, True):
            with self.assertRaises(LibraryError):
                self.library.search(query)

    def test_invalid_unsupported_truncated_and_animated_images_rejected(self):
        invalid = self.root / "bad.png"
        invalid.write_bytes(b"not an image")
        gif = self.image("unsupported.gif", "GIF")
        valid = self.image()
        truncated = self.root / "truncated.png"
        truncated.write_bytes(valid.read_bytes()[:30])
        animated = self.root / "animated.png"
        a, b = Image.new("RGB", (3, 3), "red"), Image.new("RGB", (3, 3), "blue")
        a.save(animated, save_all=True, append_images=[b], duration=100, loop=0)
        for path in (invalid, gif, truncated, animated):
            with self.subTest(path=path.name), self.assertRaises(LibraryError):
                self.library.add(path)
        self.assertEqual(self.library.list(), [])
        self.assertEqual(list(self.library.objects.iterdir()), [])

    def test_symlink_directory_oversize_pixel_and_library_limits(self):
        valid = self.image()
        link = self.root / "symlink.png"
        link.symlink_to(valid)
        for path in (link, self.root):
            with self.assertRaises(LibraryError):
                self.library.add(path)
        with patch("imagelens.library.MAX_BYTES", 5), self.assertRaises(LibraryError):
            self.library.add(valid)
        with patch("imagelens.library.MAX_PIXELS", 10), self.assertRaises(LibraryError):
            self.library.add(valid)
        with patch("imagelens.library.MAX_IMAGES", 0), self.assertRaises(LibraryError):
            self.library.add(valid)
        self.assertEqual(list(self.library.objects.iterdir()), [])

    def test_database_failure_removes_new_object_and_temp_file(self):
        with self.database() as connection:
            connection.execute("CREATE TRIGGER reject_insert BEFORE INSERT ON images BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises(LibraryError):
            self.library.add(self.image())
        self.assertEqual(self.library.list(), [])
        self.assertEqual(list(self.library.objects.iterdir()), [])

    def test_object_tampering_or_collision_never_overwritten(self):
        record = self.library.add(self.image())
        object_path = self.library.directory / record["object_path"]
        object_path.chmod(0o644)
        object_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(LibraryError, "checksum"):
            self.library.read_original(record["id"])
        with self.assertRaises(LibraryError):
            self.library.add(self.root / "sample.png")
        self.assertEqual(object_path.read_bytes(), b"tampered")

    def test_unknown_existing_schema_and_symlink_database_refused(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        with self.database(foreign / "index.sqlite3") as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
        with self.assertRaisesRegex(LibraryError, "schema"):
            Library(foreign)
        with self.database(foreign / "index.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("unrelated",)])
        linked = self.root / "linked"
        linked.mkdir()
        (linked / "index.sqlite3").symlink_to(self.library.database)
        with self.assertRaises(LibraryError):
            Library(linked)


if __name__ == "__main__":
    unittest.main()
