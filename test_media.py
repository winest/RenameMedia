import tempfile
import unittest
import errno
from pathlib import Path
from unittest.mock import patch

import CreateFoldersByDate as folders
import RenameMedia as media


class RenameTests(unittest.TestCase):
    def test_recordings_and_relative_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "LINE" / "Recordings"
            directory.mkdir(parents=True)
            cases = {
                "line_20221224-140127_\u5927\u6e56-\u5b8b.amr": "20221224_140127-\u5927\u6e56-\u5b8b.amr",
                "line_20221222-125735_Contact.amr": "20221222_125735-Contact.amr",
                "phone_20221102-115343_012345.AMR": "20221102_115343-012345.AMR",
                "line_20221104-201341.amr": "20221104_201341.amr",
            }
            with patch.object(media, "GetTimeByExif", side_effect=AssertionError("Unexpected metadata read")):
                for old, new in cases.items():
                    source = directory / old
                    source.write_bytes(b"recording")
                    with self.assertLogs(level="INFO") as logs:
                        media.TryRenameFile(
                            str(source), str(directory) + "\\", old,
                            source.stem, source.suffix, str(root),
                        )
                    self.assertTrue((directory / new).is_file())
                    self.assertIn(f"LINE\\Recordings: {old} => {new}", logs.output[0])

    def test_existing_comment_positions(self):
        for base, expected in [
            ("Note_YYY-IMG_20260123_112233-Tail", ("Note_YYY", "Tail")),
            ("IMG_20260123_112233_1", ("", "")),
            ("Trip-VID_20260123_112233", ("Trip", "")),
        ]:
            self.assertEqual(media.GetComments("", "", "", base, ".jpg"), expected)

    def test_failed_rename_has_no_success_log(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "line_20221224-140127.amr"
            path.touch()
            with patch.object(media.os, "rename", side_effect=PermissionError):
                with self.assertNoLogs(level="INFO"):
                    with self.assertRaises(PermissionError):
                        media.TryRenameFile(str(path), temp + "\\", path.name, path.stem, ".amr")


class FolderTests(unittest.TestCase):
    def test_metadata_only_cleanup_preserves_real_content_and_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "Empty" / "nested"
            child.mkdir(parents=True)
            for folder, names in [
                (child, [".nomedia", "Thumbs.db"]),
                (child.parent, [".DS_Store"]),
                (root / "Keep", [".nomedia", "desktop.ini"]),
                (root / "2020", [".nomedia"]),
            ]:
                folder.mkdir(exist_ok=True)
                for name in names:
                    (folder / name).touch()
            folders.Organize(root, dry_run=True)
            self.assertTrue((child / ".nomedia").exists())
            folders.Organize(root)
            self.assertFalse(child.parent.exists())
            self.assertTrue((root / "Keep" / ".nomedia").exists())
            self.assertTrue((root / "Keep" / "desktop.ini").exists())
            self.assertTrue((root / "2020" / ".nomedia").exists())

    def test_year_archives_are_not_scanned_moved_or_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archived = root / "2020" / "Camera" / "20200822_130903.jpg"
            archived.parent.mkdir(parents=True)
            archived.write_bytes(b"archived")
            (root / "2021").mkdir()
            loose = root / "Loose" / "20220911_010101.jpg"
            loose.parent.mkdir()
            loose.touch()
            folders.Organize(root)
            self.assertEqual(archived.read_bytes(), b"archived")
            self.assertTrue((root / "2021").is_dir())
            self.assertTrue((root / "20220911" / loose.name).exists())
            with patch.object(folders.os, "scandir", side_effect=AssertionError("Year archive scanned")):
                self.assertEqual(folders.Organize(root / "2020"), [])
                self.assertEqual(folders.Organize(archived.parent), [])

    def test_empty_folder_cleanup_is_bottom_up_and_preview_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Camera" / "nested" / "20260911_010101.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"photo")
            empty = root / "AlreadyEmpty" / "nested"
            empty.mkdir(parents=True)
            kept = [
                root / "Keep" / ".hidden",
                root / "KeepOther" / "notes.txt",
                root / "KeepDate" / "20260910",
                root / "KeepChinese" / "\u7cbe\u9078",
            ]
            for path in kept[:2]:
                path.parent.mkdir(parents=True)
                path.touch()
            for path in kept[2:]:
                path.mkdir(parents=True)
            with self.assertLogs(level="INFO") as logs:
                folders.Organize(root, dry_run=True)
            self.assertTrue(source.exists())
            self.assertTrue(empty.exists())
            self.assertFalse((root / "20260911").exists())
            removals = [line.split("Remove empty folder: ", 1)[1] for line in logs.output if "Remove empty folder: " in line]
            self.assertEqual(set(removals), {"Camera", "Camera\\nested", "AlreadyEmpty", "AlreadyEmpty\\nested"})
            self.assertLess(removals.index("Camera\\nested"), removals.index("Camera"))
            folders.Organize(root)
            self.assertFalse((root / "Camera").exists())
            self.assertFalse((root / "AlreadyEmpty").exists())
            self.assertTrue(all(path.exists() for path in kept))
            self.assertTrue(root.exists())
            self.assertEqual((root / "20260911" / source.name).read_bytes(), b"photo")

    def test_cleanup_keeps_root_even_when_completely_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a" / "b").mkdir(parents=True)
            folders.Organize(root)
            self.assertTrue(root.is_dir())
            self.assertEqual(list(root.iterdir()), [])

    def test_cleanup_does_not_hide_permission_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "empty"
            child.mkdir()
            with patch.object(Path, "rmdir", side_effect=PermissionError("Denied")):
                with self.assertRaises(PermissionError):
                    folders.RemoveEmptyFolders(root, [root, child])

    def test_cleanup_keeps_folder_when_file_arrives_during_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "empty"
            child.mkdir()
            with patch.object(Path, "rmdir", side_effect=OSError(errno.ENOTEMPTY, "Not empty")):
                with self.assertLogs(level="WARNING"):
                    self.assertEqual(folders.RemoveEmptyFolders(root, [root, child]), set())
            self.assertTrue(child.exists())

    def test_chinese_folders_and_descendants_are_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = ["\u4e91\u8ed2\u7cbe\u9078", "\u52c1\u5bf6\u5152", "Trip-\u7167\u7247", "\U00020000"]
            protected = []
            for name in names:
                path = root / "Camera" / name / "nested" / "20260910_010101.jpg"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"organized")
                protected.append(path)
            loose = root / "Camera" / "20260911_010101.jpg"
            loose.write_bytes(b"loose")
            real_scandir = folders.os.scandir
            with patch.object(folders.os, "scandir", wraps=real_scandir) as scan:
                moves = folders.Organize(root)
            self.assertEqual(len(moves), 1)
            self.assertEqual((root / "20260911" / loose.name).read_bytes(), b"loose")
            self.assertTrue(all(path.read_bytes() == b"organized" for path in protected))
            scanned = {Path(call.args[0]) for call in scan.call_args_list}
            self.assertTrue(all(path.parent not in scanned and path.parent.parent not in scanned for path in protected))
            for path in protected:
                with patch.object(folders.os, "scandir", side_effect=AssertionError("Protected folder scanned")):
                    self.assertEqual(folders.PlanMoves(path.parent.parent), [])
                    self.assertEqual(folders.PlanMoves(path.parent), [])

    def test_progress_is_logged_before_directory_listing(self):
        with tempfile.TemporaryDirectory() as temp:
            real_scandir = folders.os.scandir
            with self.assertLogs(level="INFO") as logs:
                def scan(path):
                    self.assertTrue(any("Starting preview" in line for line in logs.output))
                    self.assertTrue(any("Scanning ." in line for line in logs.output))
                    return real_scandir(path)

                with patch.object(folders.os, "scandir", side_effect=scan):
                    self.assertEqual(folders.Organize(temp, dry_run=True), [])
            self.assertTrue(any("Scan complete: 0 files" in line for line in logs.output))
            self.assertTrue(any("Preview complete: 0 files" in line for line in logs.output))

    def test_listing_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(folders.os, "scandir", side_effect=PermissionError("Denied")):
                with self.assertRaises(PermissionError):
                    folders.PlanMoves(temp)

    def test_organize_skips_existing_and_handles_collisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            originals = [
                "a\\20260910_010101.jpg",
                "b\\20260910_010101.jpg",
                "b\\Note-20260911_010101-Tail.HEIC",
                "b\\20260913_010101.jpg",
                "20260901-Trip\\20260910_020202.jpg",
                "20260801~03\\nested\\20260910_030303.jpg",
            ]
            for index, name in enumerate(originals):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bytes([index]))
            (root / "20260913").mkdir()
            invalid = root / "20260230_000000.jpg"
            invalid.touch()
            with self.assertLogs(level="WARNING"):
                plan = folders.Organize(root, dry_run=True)
            self.assertEqual(len(plan), 3)
            self.assertFalse((root / "20260910").exists())
            self.assertFalse((root / "20260911").exists())
            with self.assertLogs(level="WARNING"):
                folders.Organize(root)
            self.assertEqual((root / "20260910" / "20260910_010101.jpg").read_bytes(), b"\x00")
            self.assertEqual((root / "20260910" / "20260910_010101-1.jpg").read_bytes(), b"\x01")
            self.assertEqual((root / "20260911" / "Note-20260911_010101-Tail.HEIC").read_bytes(), b"\x02")
            self.assertTrue((root / originals[3]).exists())
            self.assertTrue((root / originals[4]).exists())
            self.assertTrue((root / originals[5]).exists())
            with self.assertLogs(level="WARNING"):
                self.assertEqual(folders.PlanMoves(root), [])
            self.assertEqual(folders.PlanMoves(root / "20260901-Trip"), [])

    def test_consecutive_days_stay_separate_across_months_and_years(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for date in ("20240228", "20240229", "20240301", "20241231", "20250101"):
                (root / (date + "_010101.jpg")).touch()
            moves = folders.Organize(root)
            self.assertEqual(
                {destination.parent.name for _, destination in moves},
                {"20240228", "20240229", "20240301", "20241231", "20250101"},
            )
            self.assertTrue(all(destination.exists() for _, destination in moves))


if __name__ == "__main__":
    unittest.main()
