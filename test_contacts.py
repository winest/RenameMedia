import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import quopri
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from RenameMedia import ContactBook, annotate_contact, load_contacts, normalize_phone, phone_match
import RenameMedia as media


class ContactTests(unittest.TestCase):
    def test_renaming_helpers_live_in_the_entry_point(self):
        root = Path(media.__file__).parent
        self.assertFalse((root / "ContactNames.py").exists())
        self.assertFalse((root / "MediaRename.py").exists())
        self.assertEqual(ContactBook.__module__, media.__name__)
        self.assertEqual(annotate_contact.__module__, media.__name__)
        self.assertEqual(media.rename_pair.__module__, media.__name__)

    def test_phone_normalization(self):
        for number in ("0912-345-678", "+886 912345678", "886912345678", "00886912345678"):
            self.assertEqual(normalize_phone(number), "+886912345678")
        self.assertEqual(normalize_phone("tel:+886-2-2345-6789;ext=123"), "+886223456789")
        self.assertEqual(normalize_phone("(02)23456789#123"), "+886223456789")
        self.assertEqual(normalize_phone("+1 (650) 555-0123"), "+16505550123")
        for number in ("23456789", "1234", "20260101", "hidden", "09AB12345678"):
            self.assertIsNone(normalize_phone(number))

    def test_vcard_encodings_and_multiple_phone_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            name = "\u6e2c\u8a66\u806f\u7d61\u4eba" * 5
            encoded = quopri.encodestring(name.encode("utf-8")).decode("ascii")
            other = "\u9280\u884c"
            big5 = quopri.encodestring(other.encode("big5")).decode("ascii")
            cards = (
                f"BEGIN:VCARD\nVERSION:2.1\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{encoded}\n"
                "TEL;CELL:+886912345678\nTEL;HOME:02-23456789\nEND:VCARD\n"
                f"BEGIN:VCARD\nVERSION:2.1\nFN;CHARSET=BIG5;ENCODING=QUOTED-PRINTABLE:{big5}\n"
                "TEL:0800123456\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:3.0\nFN:Clinic\\, Branch\\; One\n"
                "item1.TEL:0912345000\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:3.0\nFN:Folded\n  Name\nTEL:0912345001\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:3.0\nORG:Office;Support\nTEL:0912345002\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:2.1\nN:Chen;John;;;\nTEL:0912345003\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:2.1\nN:\u738b;\u5c0f\u660e;;;\nTEL:0912345004\nEND:VCARD\n"
            )
            Path(temp, "contact.vcf").write_text(cards, encoding="utf-8-sig")
            book = load_contacts(temp)
            self.assertEqual(book.lookup("0912345678"), name)
            self.assertEqual(book.lookup("0223456789"), name)
            self.assertEqual(book.lookup("0800123456"), other)
            self.assertEqual(book.lookup("0912345000"), "Clinic, Branch; One")
            self.assertEqual(book.lookup("0912345001"), "Folded Name")
            self.assertEqual(book.lookup("0912345002"), "Office Support")
            self.assertEqual(book.lookup("0912345003"), "John Chen")
            self.assertEqual(book.lookup("0912345004"), "\u738b\u5c0f\u660e")

    def test_conflicting_contacts_do_not_guess(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "contact.vcf").write_text(
                "BEGIN:VCARD\nVERSION:3.0\nFN:Alice\nTEL:02-23456789#101\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:3.0\nFN:Bob\nTEL:+886223456789;ext=102\nEND:VCARD\n"
                "BEGIN:VCARD\nVERSION:3.0\nFN:Alice\nTEL:0912345678\nTEL:+886912345678\nEND:VCARD\n",
                encoding="utf-8",
            )
            book = load_contacts(temp)
            with self.assertLogs(level="WARNING"):
                self.assertIsNone(book.lookup("0223456789"))
            self.assertEqual(book.lookup("0912345678"), "Alice")

    def test_default_override_disable_and_invalid_contacts(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(load_contacts(temp).names, {})
            missing = Path(temp, "missing.vcf")
            with self.assertRaises(FileNotFoundError):
                load_contacts(temp, missing)
            self.assertEqual(load_contacts(temp, missing, disabled=True).names, {})
            custom = Path(temp, "custom.vcf")
            custom.write_text("BEGIN:VCARD\nVERSION:3.0\nFN:Test\nTEL:0912345678\nEND:VCARD\n", encoding="utf-8")
            self.assertEqual(load_contacts(temp, custom).lookup("0912345678"), "Test")
            custom.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_contacts(temp, custom)

    def test_annotation_is_limited_to_phone_fields_and_idempotent(self):
        book = ContactBook({"+886912345678": {"Name (Office)/Support"}})
        label = "0912345678(Name \uff08Office\uff09 Support)"
        for old, new in (
            ("20260101_123456-0912345678-IN-summary", f"20260101_123456-{label}-IN-summary"),
            ("Place-20260101_123456-0912345678-OUT", f"Place-20260101_123456-{label}-OUT"),
            ("call_12-34-56_IN_0912345678", f"call_12-34-56_IN_{label}"),
            ("phone_20260101-123456_0912345678", f"phone_20260101-123456_{label}"),
            ("0912345678-20260101_123456-summary", f"{label}-20260101_123456-summary"),
        ):
            self.assertEqual(annotate_contact(old, ".m4a", book), new)
            self.assertEqual(annotate_contact(new, ".m4a", book), new)
        for stem in (
            "20260101_123456-Call 0912345678", "20260101_123456-12345678",
            "20260101_123456-0912345678(Existing)-IN",
        ):
            self.assertEqual(annotate_contact(stem, ".m4a", book), stem)
        stem = "20260101_123456-0912345678-IN"
        self.assertEqual(annotate_contact(stem, ".jpg", book), stem)
        self.assertIsNone(phone_match("20260101_123456-call_12-34-56_IN_+"))

    def test_argument_options(self):
        args = media.ParseArguments(["C:\\Media", "--vcf", "custom.vcf", "--contacts-only", "--dry-run"])
        self.assertEqual(args.vcf, "custom.vcf")
        self.assertTrue(args.contacts_only and args.dry_run)
        self.assertTrue(media.ParseArguments(["--no-contacts"]).no_contacts)
        for arguments in (
            ["--no-contacts", "--vcf", "x.vcf"],
            ["--no-contacts", "--contacts-only"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                media.ParseArguments(arguments)

    def test_legacy_call_keeps_an_existing_label_during_date_rename(self):
        stem = "Note-call_12-34-56_IN_+886912345678(Contact)-Summary"
        self.assertEqual(
            media.GetComments("", "", "", stem, ".m4a"),
            ("Note", "+886912345678(Contact)-IN-Summary"),
        )

    def test_command_loads_root_contacts_and_preserves_srt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "RenameMedia.py"
            script.write_bytes(Path(media.__file__).read_bytes())
            source = root / "20260101_123456-0912345678-IN.wav"
            source.write_bytes(b"no decoder needed")
            source.with_suffix(".srt").write_bytes(b"subtitle")
            (root / "contact.vcf").write_text(
                "BEGIN:VCARD\nVERSION:3.0\nFN:Contact\nTEL:+886912345678\nEND:VCARD\n",
                encoding="utf-8",
            )
            command = [sys.executable, str(script), temp, "--contacts-only"]
            result = subprocess.run(
                command + ["--dry-run"], stdin=subprocess.DEVNULL,
                capture_output=True, cwd=root, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(source.exists())
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL,
                capture_output=True, cwd=root, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            target = root / "20260101_123456-0912345678(Contact)-IN.wav"
            self.assertEqual(target.read_bytes(), b"no decoder needed")
            self.assertEqual(target.with_suffix(".srt").read_bytes(), b"subtitle")
            self.assertFalse(source.exists())


class PairTests(unittest.TestCase):
    def test_standalone_command_recovers_a_legacy_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            script = root / "RenameMedia.py"
            script.write_bytes(Path(media.__file__).read_bytes())
            source = root / "20260101_123456-0912345678-IN.wav"
            destination = root / "20260101_123456-0912345678(Contact)-IN.wav"
            sidecar = source.with_suffix(".srt")
            new_sidecar = destination.with_suffix(".srt")
            source.write_bytes(b"original audio")
            sidecar.write_bytes(b"original subtitles\r\n")
            source_stat, subtitle_stat = source.stat(), sidecar.stat()
            state = {
                "source": str(source), "destination": str(destination),
                "media_fingerprint": [
                    source_stat.st_size, source_stat.st_mtime_ns,
                    source_stat.st_ino, source_stat.st_dev,
                ],
                "sidecar": str(sidecar), "new_sidecar": str(new_sidecar),
                "subtitle_fingerprint": [
                    subtitle_stat.st_size, subtitle_stat.st_mtime_ns,
                    subtitle_stat.st_ino, subtitle_stat.st_dev,
                ],
                "subtitle_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
            }
            journal = root / "Logs" / "RenameTransactions" / "legacy.pending.json"
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps(state), encoding="utf-8")
            source.rename(destination)
            result = subprocess.run(
                [sys.executable, str(script), str(root), "--contacts-only"],
                stdin=subprocess.DEVNULL, capture_output=True, cwd=root, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(source.exists())
            self.assertFalse(sidecar.exists())
            self.assertEqual(destination.read_bytes(), b"original audio")
            self.assertEqual(new_sidecar.read_bytes(), b"original subtitles\r\n")
            self.assertFalse(journal.exists())
            self.assertTrue(journal.with_name("legacy.done.json").is_file())

    def test_rename_lock_excludes_concurrent_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            with media.exclusive_renames(temp):
                with self.assertRaises(RuntimeError):
                    with media.exclusive_renames(temp):
                        self.fail("Concurrent rename lock acquired")
            with media.exclusive_renames(temp):
                pass

    def test_existing_and_legacy_names_move_subtitles_without_metadata_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            book = ContactBook({"+886912345678": {"Contact"}})
            for old in ("20260101_123456-0912345678-IN.m4a", "phone_20260102-123456_0912345678.amr"):
                source = root / old
                source.write_bytes(b"media")
                subtitle = source.with_suffix(".srt")
                subtitle.write_bytes(b"unchanged\r\nsubtitle")
                before = media.fingerprint(source)
                with patch.object(media, "GetTimeByExif", side_effect=AssertionError("Metadata read")):
                    media.TryRenameFile(
                        str(source), str(root), source.name, source.stem, source.suffix,
                        contacts=book, journal_dir=root / "journals",
                    )
                renamed = next(root.glob("*Contact*" + source.suffix))
                self.assertEqual(media.fingerprint(renamed), before)
                self.assertEqual(renamed.with_suffix(".srt").read_bytes(), b"unchanged\r\nsubtitle")
                self.assertFalse(source.exists())
                self.assertFalse(subtitle.exists())

    def test_contacts_only_and_dry_run_do_not_change_dates_or_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "phone_20260101-123456_0912345678.amr"
            source.write_bytes(b"media")
            book = ContactBook({"+886912345678": {"Contact"}})
            with patch.object(media, "ParseRecording", side_effect=AssertionError("Date parsed")):
                for preview in (True, False):
                    media.TryRenameFile(
                        str(source), temp, source.name, source.stem, source.suffix,
                        contacts=book, contacts_only=True, dry_run=preview, journal_dir=root / "journals",
                    )
                    self.assertEqual(source.exists(), preview)
                    self.assertEqual((root / "journals").exists(), not preview)
            self.assertTrue((root / "phone_20260101-123456_0912345678(Contact).amr").is_file())

    def test_collision_checks_subtitles_and_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "target.srt").write_bytes(b"another subtitle")
            (root / "target-1.m4a").mkdir()
            (root / "target-2.srt").mkdir()
            self.assertEqual(media.choose_destination(root, "target", ".m4a").name, "target-3.m4a")
            with self.assertRaises(ValueError):
                media.choose_destination(root, "a" * 255, ".m4a")

    def test_shared_subtitle_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for suffix in (".mp4", ".mov", ".srt"):
                (root / ("source" + suffix)).write_bytes(b"content")
            with self.assertRaises(ValueError):
                media.rename_pair(root / "source.mp4", root / "target.mp4")
            self.assertTrue((root / "source.mp4").exists())
            self.assertTrue((root / "source.srt").exists())

    def test_subtitle_rename_failure_rolls_back_media(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "source.amr", root / "target.amr"
            source.write_bytes(b"media")
            source.with_suffix(".srt").write_bytes(b"subtitle")
            real_rename = os.rename

            def fail_subtitle(old, new):
                if Path(old) == source.with_suffix(".srt"):
                    raise PermissionError("Subtitle is locked")
                real_rename(old, new)

            with patch.object(media.os, "rename", side_effect=fail_subtitle):
                with self.assertRaises(PermissionError):
                    media.rename_pair(source, destination, journal_dir=root / "journals")
            self.assertTrue(source.exists())
            self.assertTrue(source.with_suffix(".srt").exists())
            self.assertFalse(destination.exists())
            self.assertEqual(len(list((root / "journals").glob("*.rolled-back.json"))), 1)

    def test_interrupted_pair_recovery_is_scoped_and_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "source.amr", root / "target.amr"
            source.write_bytes(b"media")
            source.with_suffix(".srt").write_bytes(b"subtitle")
            state = media.pair_state(source, destination)
            journals = root / "journals"
            journals.mkdir()
            media.write_journal(journals / "test.pending.json", state)
            os.rename(source, destination)
            media.recover_pending(journals, root / "unrelated")
            media.recover_pending(journals, root, dry_run=True)
            self.assertTrue(source.with_suffix(".srt").exists())
            media.recover_pending(journals, root)
            self.assertEqual(destination.with_suffix(".srt").read_bytes(), b"subtitle")
            self.assertFalse(source.with_suffix(".srt").exists())
            self.assertTrue((journals / "test.done.json").is_file())

    def test_recovery_rejects_changed_subtitle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "source.amr", root / "target.amr"
            source.write_bytes(b"media")
            source.with_suffix(".srt").write_bytes(b"subtitle")
            state = media.pair_state(source, destination)
            source.with_suffix(".srt").write_bytes(b"edited")
            with self.assertRaises(ValueError):
                media.finish_pair(state)
            self.assertEqual(source.with_suffix(".srt").read_bytes(), b"edited")
            self.assertFalse(destination.with_suffix(".srt").exists())


if __name__ == "__main__":
    unittest.main()
