from array import array
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import av
import requests

import TranscribeMedia as transcribe
from TranscribeMedia import (
    RATE, SCOPE, AzureRequestError, BulkRun, ProfileCliCredential, RequestCache,
    TitleUnavailable, UnknownRequest, aac_config_prefix, audio_chunks,
    audio_decode_source, bulk_title_body, choose_pair_paths, digest,
    embedded_timestamp, exclusive_run, fingerprint, is_single_filler,
    parse_title_response, publish_pair, render_srt, safe_title, sample_identity,
    save_json, select_audio, speech_request, srt_time, target_stem,
)


class CommandTests(unittest.TestCase):
    def setUp(self):
        configuration = patch.object(transcribe.logging, "basicConfig")
        configuration.start()
        self.addCleanup(configuration.stop)

    def command(self, name, source, cache):
        args = [
            name, str(source), "--cache", str(cache),
            "--speech-config", "unused-speech.json",
            "--text-config", "unused-title.py",
        ]
        if name == "run":
            args.extend(["--text-tenant", "test-tenant"])
        return args

    def test_single_entry_point_help_needs_no_optional_dependencies(self):
        script = Path(transcribe.__file__)
        self.assertFalse(script.with_name("BulkTranscribe.py").exists())
        for command in ([], ["inventory"], ["samples"], ["run"]):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "-S", str(script), *command, "--help"],
                    capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"usage:", result.stdout)
                self.assertNotIn(b"--apply", result.stdout)

    def test_sample_and_batch_defaults_are_preserved(self):
        parser = transcribe.create_parser()
        samples = parser.parse_args(self.command("samples", "selection.json", "cache"))
        self.assertEqual(samples.speech_mode, "fast")
        self.assertEqual(samples.locale, "zh-TW")
        self.assertFalse(samples.short_subtitles)
        self.assertIsNone(samples.text_tenant)
        batch = parser.parse_args(self.command("run", "inventory.json", "cache"))
        self.assertEqual(batch.locale, "auto")
        self.assertEqual(batch.region, "eastus")
        self.assertEqual(batch.workers, 6)
        self.assertEqual(batch.chunk_seconds, 600)
        self.assertFalse(hasattr(batch, "apply"))
        self.assertFalse(batch.retry_uncertain)
        self.assertIsNone(batch.only)
        self.assertIsNone(batch.speech_key_env)

    def test_run_executes_batch_without_an_apply_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / "inventory.json"
            data = {"root": temp, "files": []}
            inventory.write_text(json.dumps(data), encoding="utf-8")
            cache = root / "cache"
            with patch.object(transcribe, "exclusive_run", return_value=contextlib.nullcontext()) as lock:
                with patch.object(transcribe.logging, "FileHandler"):
                    with patch.object(transcribe, "render_srt"):
                        with patch.object(transcribe, "BulkRun") as batch:
                            batch.return_value.run.return_value = 7
                            result = transcribe.main(self.command("run", inventory, cache))
            self.assertEqual(result, 7)
            lock.assert_called_once_with(cache)
            batch.assert_called_once()
            self.assertEqual(batch.call_args.args[1], data)
            self.assertFalse(hasattr(batch.call_args.args[0], "apply"))
            batch.return_value.run.assert_called_once_with()
            self.assertFalse(cache.exists())

    def test_removed_apply_flag_is_rejected_before_execution(self):
        with patch.object(transcribe, "run_batch") as batch:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    transcribe.main(
                        self.command("run", "inventory.json", "cache") + ["--apply"],
                    )
        self.assertEqual(error.exception.code, 2)
        batch.assert_not_called()

    def test_inventory_and_samples_route_to_existing_workflows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "inventory.json"
            with patch.object(transcribe, "inventory") as scan:
                self.assertEqual(transcribe.main([
                    "inventory", temp, "--output", str(output),
                ]), 0)
            scan.assert_called_once_with(root.resolve(), output)
            selection = [{"path": "sample.mp3", "seconds": 1}]
            output.write_text(json.dumps(selection), encoding="utf-8")
            with patch.object(transcribe, "process_samples") as samples:
                self.assertEqual(transcribe.main(
                    self.command("samples", output, root / "cache"),
                ), 0)
            self.assertEqual(samples.call_args.args[0], selection)
            self.assertEqual(samples.call_args.args[-3:], ("fast", "zh-TW", False))

    def test_run_routes_options_and_returns_batch_exit_code(self):
        with patch.object(transcribe, "run_batch", return_value=7) as batch:
            result = transcribe.main(
                self.command("run", "inventory.json", "cache") +
                ["--workers", "8", "--retry-uncertain", "--only", "sample.mp3"],
            )
        self.assertEqual(result, 7)
        args = batch.call_args.args[0]
        self.assertTrue(args.retry_uncertain)
        self.assertEqual(args.workers, 8)
        self.assertEqual(args.only, ["sample.mp3"])

    def test_invalid_batch_limits_fail_before_reading_files(self):
        for option, value in (
            ("--workers", "0"), ("--chunk-seconds", "9"), ("--chunk-seconds", "601"),
        ):
            with self.subTest(option=option, value=value):
                with patch.object(transcribe, "run_batch") as batch:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as error:
                            transcribe.main(
                                self.command("run", "missing.json", "cache") + [option, value],
                            )
                self.assertEqual(error.exception.code, 2)
                batch.assert_not_called()

    def test_missing_inventory_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertLogs(level="ERROR"):
                result = transcribe.main(self.command(
                    "run", root / "missing.json", root / "cache",
                ))
            self.assertEqual(result, 1)
            self.assertFalse((root / "cache").exists())


class LegacyCacheTests(unittest.TestCase):
    def test_legacy_sample_and_request_keys_are_unchanged(self):
        self.assertEqual(
            sample_identity(
                r"C:\Media\20220101_010203.wav", 10, 20,
                speech_request("https://speech.example"),
            ),
            "58c2758435d81fabb874",
        )
        identity = {
            "request": speech_request("https://speech.example", "mai-transcribe-2", "auto"),
            "pcm_sha256": "legacy-audio",
        }
        key = "38290b6853bfa7cc5db79014f75124044d54dcd6cae2bb8f1823115d0d53475e"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = {"phrases": []}
            transcribe.save_json(root / (key + ".json"), {"value": result})
            before = (root / (key + ".json")).read_bytes()
            cache = transcribe.RequestCache(root, threading.Event())
            with patch.object(transcribe, "post_checked", side_effect=AssertionError("Network request")):
                with patch.object(transcribe, "Tokens", side_effect=AssertionError("Credentials")) as prepare:
                    self.assertEqual(cache.perform("speech", identity, prepare), (key, result))
            self.assertEqual((root / (key + ".json")).read_bytes(), before)

    def test_batch_configuration_remains_compatible(self):
        data = {"root": r"C:\Media", "files": []}
        config = SimpleNamespace(
            endpoint="https://title.example", responses_api_version="test-api",
            text_model="test-model",
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            speech = root / "speech.json"
            speech.write_text(json.dumps({
                "azure": {"speech_endpoints": {"eastus": "https://speech.example"}},
            }), encoding="utf-8")
            args = transcribe.create_parser().parse_args([
                "run", "inventory.json", "--speech-config", str(speech),
                "--text-config", "unused.py", "--text-tenant", "test-tenant",
                "--cache", temp,
            ])
            with patch.object(transcribe, "Tokens"):
                with patch.object(transcribe, "load_text_config", return_value=config):
                    run = transcribe.BulkRun(args, data)
                    path = root / "configuration.json"
                    before = path.read_bytes()
                    self.assertEqual(
                        transcribe.digest(transcribe.read_json(path)),
                        "4917072138b518c69ca49b083c6e1449aa5ae61298e0688e69130ecd4b37e235",
                    )
                    transcribe.BulkRun(args, data)
                    self.assertEqual(path.read_bytes(), before)
            self.assertEqual(run.speech["definition"]["enhancedMode"]["model"], "MAI-Transcribe-2")

    def test_completed_renamed_job_is_reused_without_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "20220101_010203-0912345678.m4a"
            source.write_bytes(b"original audio")
            stat = transcribe.fingerprint(source)
            row = {"path": str(source), "size": stat["size"], "mtime_ns": stat["mtime_ns"]}
            destination = source.with_name("20220101_010203-0912345678(Contact)-Title.m4a")
            source.rename(destination)
            sidecar = destination.with_suffix(".srt")
            sidecar.write_bytes(b"existing subtitles")
            cache = root / "cache"
            (cache / "jobs").mkdir(parents=True)
            key = transcribe.digest([str(source), row["size"], row["mtime_ns"]])
            path = cache / "jobs" / (key + ".json")
            transcribe.save_json(path, {
                "source": str(source), "destination": str(destination),
                "sidecar": str(sidecar), "phase": "done", "fingerprint": stat,
                "subtitle_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                "contact_annotation": {"name": "Contact"},
            })
            before = path.read_bytes()
            run = SimpleNamespace(
                root=root, cache=cache, status_lock=threading.Lock(), active={},
            )
            with patch.object(transcribe, "post_checked", side_effect=AssertionError("Network request")):
                self.assertEqual(transcribe.BulkRun.process(run, row), "done")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(destination.read_bytes(), b"original audio")
            self.assertEqual(sidecar.read_bytes(), b"existing subtitles")
            self.assertFalse(source.exists())

    def test_sample_cache_is_reused_without_authentication_or_renaming(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "20220101_010203.mp3"
            source.write_bytes(b"original audio")
            stat = source.stat()
            selection = [{
                "path": str(source), "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns, "seconds": 1,
            }]
            request = speech_request("https://speech.example")
            identity = sample_identity(source, stat.st_size, stat.st_mtime_ns, request)
            cache = root / "cache"
            cache.mkdir()
            result = {"phrases": [{
                "text": "Hello", "offsetMilliseconds": 0, "durationMilliseconds": 1000,
            }]}
            transcribe.save_json(cache / (identity + ".json"), {
                "speech_request": request, "transcription": result,
                "offset_ms": 0, "title": "Cached title",
            })
            config_path = root / "speech.json"
            config_path.write_text(json.dumps({
                "azure": {"speech_endpoints": {"eastus": "https://speech.example"}},
            }), encoding="utf-8")
            with patch("azure.identity.AzureCliCredential") as credential:
                credential.return_value.get_token.side_effect = AssertionError("Authentication")
                with patch.object(transcribe, "load_text_config", return_value=SimpleNamespace()):
                    with patch.object(transcribe, "post_checked", side_effect=AssertionError("Network request")):
                        transcribe.process_samples(
                            selection, config_path, root / "unused.py", cache, "eastus",
                        )
            self.assertEqual(source.read_bytes(), b"original audio")
            self.assertFalse(source.with_suffix(".srt").exists())
            self.assertIn("Hello", (cache / (identity + ".srt")).read_text(encoding="utf-8"))
            self.assertEqual(
                transcribe.read_json(cache / (identity + ".json"))["transcription"], result,
            )


class SubtitleTests(unittest.TestCase):
    def test_default_request_uses_taiwan_chinese(self):
        request = speech_request("https://speech.example/")
        self.assertEqual(request["definition"], {
            "locales": ["zh-TW"], "profanityFilterMode": "None",
        })
        self.assertIn("?api-version=2025-10-15", request["url"])
        self.assertEqual(
            speech_request("https://speech.example", locale="auto")["definition"]["locales"], [],
        )
        with self.assertRaises(ValueError):
            speech_request("https://speech.example", mode="unknown")
        with self.assertRaises(ValueError):
            speech_request("https://speech.example", locale="")

    def test_enhanced_models_use_documented_options(self):
        llm = speech_request("https://speech.example", "llm")["definition"]
        self.assertEqual(llm["locales"], ["zh-TW"])
        self.assertTrue(llm["enhancedMode"]["enabled"])
        self.assertEqual(llm["enhancedMode"]["task"], "transcribe")
        self.assertIn("Traditional Chinese", llm["enhancedMode"]["prompt"][0])
        mai = speech_request("https://speech.example", "mai-transcribe-2")["definition"]
        self.assertEqual(mai["locales"], ["zh"])
        self.assertEqual(mai["enhancedMode"]["model"], "MAI-Transcribe-2")
        self.assertEqual(mai["enhancedMode"]["modelOptions"], {
            "timestamps": "word", "transcribeStyle": "verbatim",
        })
        self.assertNotIn("prompt", mai["enhancedMode"])

    def test_request_settings_separate_paid_result_caches(self):
        request = speech_request("https://speech.example")
        identity = sample_identity("sample.mp3", 10, 20, request)
        reordered = dict(reversed(list(request.items())))
        self.assertEqual(identity, sample_identity("sample.mp3", 10, 20, reordered))
        variants = [
            speech_request("https://speech.example", locale="auto"),
            speech_request("https://speech.example", "llm"),
            speech_request("https://speech.example", "mai-transcribe-2"),
            speech_request("https://other.example"),
        ]
        for variant in variants:
            self.assertNotEqual(identity, sample_identity("sample.mp3", 10, 20, variant))
        self.assertNotEqual(identity, sample_identity("sample.mp3", 11, 20, request))
        self.assertNotEqual(identity, sample_identity("sample.mp3", 10, 21, request))

    def test_traditional_subtitles_preserve_raw_response_and_timing(self):
        result = {"phrases": [{
            "text": "\u8fd9\u4e2a\u706f\u574f\u4e86 LED OK",
            "offsetMilliseconds": 500, "durationMilliseconds": 1000,
        }]}
        original = json.dumps(result)
        text = render_srt(result)
        self.assertIn("\u9019\u500b\u71c8\u58de\u4e86 LED OK", text)
        self.assertIn("00:00:00,500 --> 00:00:01,500", text)
        self.assertEqual(json.dumps(result), original)

    def test_short_subtitles_preserve_text_and_use_word_timestamps(self):
        words = [
            {"text": word, "offsetMilliseconds": index * 1000, "durationMilliseconds": 900}
            for index, word in enumerate(["One", "two", "three,", "four", "five", "six", "seven", "eight."])
        ]
        result = {"phrases": [{
            "text": "One two three, four five six seven eight.",
            "offsetMilliseconds": 0, "durationMilliseconds": 8000, "words": words,
        }]}
        original = json.dumps(result)
        text = render_srt(result, offset_ms=100, short_cues=True)
        self.assertIn("00:00:00,100 --> 00:00:03,000\nOne two three,", text)
        self.assertIn("00:00:03,100 --> 00:00:08,000\nfour five six seven eight.", text)
        self.assertEqual(json.dumps(result), original)

    def test_short_subtitles_enforce_duration_and_character_limits(self):
        word = "\u4f60"
        result = {"phrases": [{
            "text": word * 70, "offsetMilliseconds": 0, "durationMilliseconds": 14000,
            "words": [
                {"text": word, "offsetMilliseconds": i * 200, "durationMilliseconds": 200}
                for i in range(70)
            ],
        }]}
        text = render_srt(result, short_cues=True)
        blocks = text.strip().split("\n\n")
        self.assertEqual([len(block.splitlines()[2]) for block in blocks], [30, 30, 10])
        self.assertIn("00:00:00,000 --> 00:00:06,000", text)
        result["phrases"][0]["words"] = [
            {"text": word, "offsetMilliseconds": i * 50, "durationMilliseconds": 50}
            for i in range(70)
        ]
        blocks = render_srt(result, short_cues=True).strip().split("\n\n")
        self.assertEqual([len(block.splitlines()[2]) for block in blocks], [32, 32, 6])

    def test_shared_word_lists_align_each_phrase_to_its_own_time(self):
        words = [
            {"text": "First.", "offsetMilliseconds": 1000, "durationMilliseconds": 500},
            {"text": "Second.", "offsetMilliseconds": 3000, "durationMilliseconds": 500},
        ]
        result = {"phrases": [
            {"text": "Second.", "offsetMilliseconds": 0, "durationMilliseconds": 4000, "words": words},
            {"text": "First.", "offsetMilliseconds": 0, "durationMilliseconds": 4000, "words": words},
        ]}
        text = render_srt(result, short_cues=True)
        self.assertIn("1\n00:00:01,000 --> 00:00:01,500\nFirst.", text)
        self.assertIn("2\n00:00:03,000 --> 00:00:03,500\nSecond.", text)

    def test_short_subtitles_do_not_guess_missing_or_ambiguous_alignment(self):
        cases = [
            [],
            [{"text": "Test"}],
            [{"text": "Test", "offsetMilliseconds": None, "durationMilliseconds": 1000}],
            [{"text": "Test", "offsetMilliseconds": float("nan"), "durationMilliseconds": 1000}],
            [{"text": "Different", "offsetMilliseconds": 0, "durationMilliseconds": 1000}],
            [
                {"text": "Test", "offsetMilliseconds": 0, "durationMilliseconds": 1000},
                {"text": "Test", "offsetMilliseconds": 1000, "durationMilliseconds": 1000},
            ],
            [{"text": "Testing", "offsetMilliseconds": 0, "durationMilliseconds": 1000}],
            [{"text": "Test", "offsetMilliseconds": 0, "durationMilliseconds": 0}],
        ]
        for words in cases:
            result = {"phrases": [{
                "text": "Test", "offsetMilliseconds": 0, "durationMilliseconds": 2000, "words": words,
            }]}
            with self.subTest(words=words), self.assertLogs(level="WARNING"):
                self.assertEqual(render_srt(result, short_cues=True), render_srt(result))

    def test_short_subtitles_preserve_punctuation_and_traditional_characters(self):
        result = {"phrases": [{
            "text": "\u201c\u8fd9\u4e2a\u706f\u574f\u4e86\uff01\u201d LED OK.",
            "offsetMilliseconds": 0, "durationMilliseconds": 4000,
            "words": [
                {"text": "\u8fd9\u4e2a", "offsetMilliseconds": 0, "durationMilliseconds": 500},
                {"text": "\u706f", "offsetMilliseconds": 500, "durationMilliseconds": 500},
                {"text": "\u574f\u4e86", "offsetMilliseconds": 1000, "durationMilliseconds": 500},
                {"text": "led", "offsetMilliseconds": 2500, "durationMilliseconds": 500},
                {"text": "ok", "offsetMilliseconds": 3000, "durationMilliseconds": 500},
            ],
        }]}
        text = render_srt(result, short_cues=True)
        self.assertIn("\u201c\u9019\u500b\u71c8\u58de\u4e86\uff01\u201d", text)
        self.assertIn("00:00:02,500 --> 00:00:03,500\nLED OK.", text)

    def test_separate_cli_profile_does_not_change_process_environment(self):
        old = os.environ.get("AZURE_CONFIG_DIR")
        result = SimpleNamespace(returncode=0, stdout=json.dumps({
            "accessToken": "test-token", "expires_on": int(time.time()) + 3600,
        }))
        credential = ProfileCliCredential("test-tenant", "test-profile")
        with patch("TranscribeMedia.shutil.which", return_value="az.cmd"):
            with patch("TranscribeMedia.subprocess.run", return_value=result) as run:
                self.assertEqual(credential.get_token(SCOPE).token, "test-token")
                self.assertEqual(credential.get_token(SCOPE).token, "test-token")
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["env"]["AZURE_CONFIG_DIR"], credential.profile)
        self.assertEqual(os.environ.get("AZURE_CONFIG_DIR"), old)
        self.assertIn("test-tenant", run.call_args.args[0])

    def test_timestamp_rounding_and_long_hours(self):
        self.assertEqual(srt_time(999.6), "00:00:01,000")
        self.assertEqual(srt_time(3600000), "01:00:00,000")
        with self.assertRaises(ValueError):
            srt_time(-1)

    def test_sorted_phrases_and_audio_offset(self):
        result = {
            "phrases": [
                {"text": "Second.", "offsetMilliseconds": 1500, "durationMilliseconds": 500},
                {"text": "\u4f60\u597d Hello.", "offsetMilliseconds": 0, "durationMilliseconds": 1000},
            ]
        }
        text = render_srt(result, 200)
        self.assertIn("1\n00:00:00,200 --> 00:00:01,200\n\u4f60\u597d Hello.", text)
        self.assertIn("2\n00:00:01,700 --> 00:00:02,200\nSecond.", text)

    def test_silence_is_empty_not_an_invented_transcript(self):
        self.assertEqual(render_srt({"phrases": [], "combinedPhrases": []}), "")
        with self.assertRaises(ValueError):
            render_srt({"combinedPhrases": [{"text": "Missing timing"}]})
        with self.assertRaises(ValueError):
            render_srt({"phrases": [], "combinedPhrases": [{"text": "Missing timing"}]})

    def test_invalid_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            render_srt({"phrases": [{"text": "Test", "offsetMilliseconds": 0, "durationMilliseconds": 0}]})

    def test_filename_title_sanitization(self):
        self.assertEqual(safe_title(' Test: a/b?\n '), "Test a b")
        with self.assertRaises(ValueError):
            safe_title("???")
        with self.assertRaises(ValueError):
            safe_title("a" * 81)


class NamingTests(unittest.TestCase):
    def test_generated_titles_are_converted_to_traditional_chinese(self):
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{
                "type": "output_text", "text": "\u4ece\u95e8\u524d\u8d70\u8fc7",
            }]}],
        }
        title, _ = parse_title_response(response)
        self.assertEqual(title, "\u5f9e\u9580\u524d\u8d70\u904e")
        self.assertEqual(response["output"][0]["content"][0]["text"], "\u4ece\u95e8\u524d\u8d70\u8fc7")

    def test_title_policy_abstains_instead_of_naming_missing_content(self):
        config = SimpleNamespace(text_model="test-model")
        body = bulk_title_body("A transcript", config)
        self.assertIn("NO_TITLE", body["instructions"])
        self.assertIn("placeholder", body["instructions"])
        self.assertEqual(BulkRun.title(None, "OK"), (None, None))
        self.assertEqual(BulkRun.title(None, "\u597d"), (None, None))

    def test_phone_direction_and_location_are_preserved(self):
        self.assertEqual(
            target_stem(Path("20190706_174315-0228839919-IN.m4a"), "Summary"),
            "20190706_174315-0228839919-IN-Summary",
        )
        self.assertEqual(
            target_stem(Path("Place-20210717_100000.m4a"), "Summary"),
            "20210717_100000-Place-Summary",
        )
        self.assertEqual(
            target_stem(Path("Place.m4a"), "Summary", "20210717_100000"),
            "20210717_100000-Place-Summary",
        )
        self.assertEqual(target_stem(Path("Place.m4a"), "Summary"), "Place-Summary")
        self.assertEqual(target_stem(Path("Place.m4a"), None), "Place")

    def test_dates_and_missing_direction_are_not_invented(self):
        self.assertEqual(
            target_stem(Path("20220101_000000-0912345678.amr"), "Summary"),
            "20220101_000000-0912345678-Summary",
        )
        with self.assertRaises(ValueError):
            target_stem(Path("20220230_010000.m4a"), "Summary")
        with self.assertRaises(ValueError):
            target_stem(Path("20220101_000000-20220102_000000.m4a"), "Summary")

    def test_embedded_timestamp_rejects_placeholder_years(self):
        container = SimpleNamespace(metadata={"creation_time": "1904-01-01T00:00:00Z"})
        stream = SimpleNamespace(metadata={})
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(embedded_timestamp(container, stream))
        container.metadata = {"creation_time": "2021-07-17T10:20:30"}
        self.assertEqual(embedded_timestamp(container, stream), "20210717_102030")

    def test_only_single_fillers_are_omitted(self):
        for value in ("\u55ef", "\u563f\uff01", "\u5594?", " um "):
            self.assertTrue(is_single_filler(value))
        for value in ("\u597d", "\u55ef\u55ef", "\u55ef\uff0c\u6211\u77e5\u9053", "okay"):
            self.assertFalse(is_single_filler(value))
        result = {"phrases": [
            {"text": "\u55ef", "offsetMilliseconds": 0, "durationMilliseconds": 200},
            {"text": "\u597d", "offsetMilliseconds": 1000, "durationMilliseconds": 200},
        ]}
        raw = json.dumps(result)
        subtitle = render_srt(result, omit_single_fillers=True)
        self.assertEqual(subtitle, "1\n00:00:01,000 --> 00:00:01,200\n\u597d\n")
        self.assertEqual(json.dumps(result), raw)
        result["phrases"] = result["phrases"][:1]
        result["combinedPhrases"] = [{"text": "\u55ef"}]
        self.assertEqual(render_srt(result, omit_single_fillers=True), "")


class RequestTests(unittest.TestCase):
    def test_active_transport_failure_has_bounded_opt_in_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            stop = threading.Event()
            cache = RequestCache(Path(temp), stop, retry_uncertain=True)
            calls = []

            def send():
                calls.append(1)
                if len(calls) == 1:
                    raise requests.ConnectionError("Connection reset")
                return {"phrases": []}

            with patch.object(stop, "wait", return_value=False), self.assertLogs(level="WARNING"):
                _, value = cache.perform("speech", {}, lambda: send)
            self.assertEqual(value, {"phrases": []})
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list(Path(temp).glob("*.unknown-*.json"))), 1)

    def test_exhausted_transport_retries_remain_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            stop = threading.Event()
            cache = RequestCache(Path(temp), stop, retry_uncertain=True)
            calls = []

            def send():
                calls.append(1)
                raise requests.ConnectionError("Connection reset")

            with patch.object(stop, "wait", return_value=False), self.assertLogs(level="WARNING"):
                with self.assertRaises(UnknownRequest):
                    cache.perform("speech", {}, lambda: send)
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(list(Path(temp).glob("*.pending.json"))), 1)

    def test_failed_auth_preparation_does_not_clear_uncertain_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event())

            def send():
                raise requests.ConnectionError("Connection reset")

            with self.assertRaises(UnknownRequest):
                cache.perform("speech", {}, lambda: send)
            cache.retry_uncertain = True

            def prepare():
                raise RuntimeError("Authentication unavailable")

            with self.assertLogs(level="WARNING"), self.assertRaises(RuntimeError):
                cache.perform("speech", {}, prepare)
            self.assertEqual(len(list(Path(temp).glob("*.pending.json"))), 1)

    def test_content_filter_rejections_are_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event(), retry_uncertain=True)
            calls = []

            def send():
                calls.append(1)
                raise AzureRequestError(400, code="content_filter")

            for _ in range(2):
                with self.assertRaises(AzureRequestError):
                    cache.perform("title", {}, lambda: send)
            self.assertEqual(len(calls), 1)

    def test_filtered_title_is_not_used_as_a_completed_title(self):
        class FilteredCache:
            def perform(self, *args):
                return "cached", {
                    "status": "incomplete", "incomplete_details": {"reason": "content_filter"},
                    "output": [],
                }

        run = SimpleNamespace(
            text_config=SimpleNamespace(text_model="test"),
            text_url="https://example.test/responses", requests=FilteredCache(),
        )
        with self.assertRaises(TitleUnavailable):
            BulkRun.title(run, "A real recording subject")

    def test_parallel_identical_requests_are_paid_once(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event())
            calls = []

            def prepare():
                def send():
                    calls.append(1)
                    time.sleep(.02)
                    return {"phrases": []}
                return send

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(
                    lambda _: cache.perform("speech", {"audio": "same"}, prepare, 10),
                    range(4),
                ))
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(result == results[0] for result in results))
            saved = json.loads((Path(temp) / (results[0][0] + ".json")).read_text())
            self.assertEqual(saved["audio_seconds"], 10)

    def test_uncertain_requests_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event())
            calls = []

            def prepare():
                def send():
                    calls.append(1)
                    raise requests.ConnectionError("Connection interrupted")
                return send

            for _ in range(2):
                with self.assertRaises(UnknownRequest):
                    cache.perform("speech", {}, prepare)
            self.assertEqual(len(calls), 1)

    def test_explicit_rejection_can_be_retried_after_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event())

            def reject():
                raise AzureRequestError(401)

            with self.assertRaises(AzureRequestError):
                cache.perform("speech", {}, lambda: reject)
            _, result = cache.perform("speech", {}, lambda: lambda: {"phrases": []})
            self.assertEqual(result, {"phrases": []})

    def test_explicit_uncertain_retry_retains_audit_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = RequestCache(Path(temp), threading.Event())

            def fail():
                raise requests.ConnectionError("Connection interrupted")

            with self.assertRaises(UnknownRequest):
                cache.perform("speech", {}, lambda: fail)
            retry = RequestCache(Path(temp), threading.Event(), retry_uncertain=True)
            with self.assertLogs(level="WARNING"):
                _, result = retry.perform("speech", {}, lambda: lambda: {"phrases": []})
            self.assertEqual(result, {"phrases": []})
            self.assertEqual(len(list(Path(temp).glob("*.unknown-*.json"))), 1)


class PersistenceTests(unittest.TestCase):
    def test_deleted_inputs_are_not_recreated_and_replacements_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "deleted.m4a"
            cache = root / "cache"
            (cache / "jobs").mkdir(parents=True)
            row = {"path": str(source), "size": 0, "mtime_ns": 0}
            key = digest([str(source), 0, 0])
            save_json(cache / "jobs" / (key + ".json"), {
                "source": str(source), "phase": "deleted",
            })
            run = SimpleNamespace(
                root=root, cache=cache, status_lock=threading.Lock(), active={},
            )
            self.assertEqual(BulkRun.process(run, row), "deleted")
            source.write_bytes(b"replacement")
            with self.assertLogs(level="ERROR"):
                self.assertEqual(BulkRun.process(run, row), "failed")
            self.assertEqual(source.read_bytes(), b"replacement")

    def denied(self):
        error = PermissionError("File is temporarily locked")
        error.winerror = 5
        return error

    @unittest.skipUnless(os.name == "nt", "Windows sharing failures")
    def test_atomic_write_retries_a_temporary_reader_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"old": true}')
            replace = os.replace
            attempts = []

            def retry(source, destination):
                attempts.append(1)
                if len(attempts) == 1:
                    raise self.denied()
                replace(source, destination)

            with patch("TranscribeMedia.os.replace", side_effect=retry):
                with patch("TranscribeMedia.time.sleep"):
                    save_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            self.assertEqual(len(attempts), 2)

    @unittest.skipUnless(os.name == "nt", "Windows sharing failures")
    def test_persistent_write_failure_preserves_old_and_temporary_states(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"old": true}')
            with patch("TranscribeMedia.os.replace", side_effect=self.denied()) as replace:
                with patch("TranscribeMedia.time.sleep"), self.assertRaises(PermissionError):
                    save_json(path, {"new": True})
            self.assertEqual(replace.call_count, 8)
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(json.loads(path.with_suffix(".json.tmp").read_text()), {"new": True})

    def test_locked_progress_snapshot_does_not_abort_workers(self):
        run = SimpleNamespace(
            status_lock=threading.Lock(), statuses={0: "done"}, active={},
            stop=threading.Event(), cache=Path("unused"),
        )
        with patch("TranscribeMedia.save_json", side_effect=self.denied()):
            with self.assertLogs(level="WARNING") as logs:
                BulkRun.progress(run, 2)
        self.assertIn("run.log remains current", logs.output[0])
        self.assertFalse(run.stop.is_set())

    def test_progress_writes_are_throttled_but_final_state_is_flushed(self):
        run = SimpleNamespace(
            status_lock=threading.Lock(), statuses={}, active={},
            stop=threading.Event(), cache=Path("unused"),
        )
        with patch("TranscribeMedia.time.monotonic", side_effect=[100, 101, 106, 107]):
            with patch("TranscribeMedia.save_json") as save:
                BulkRun.progress(run, 2)
                BulkRun.progress(run, 2)
                BulkRun.progress(run, 2)
                BulkRun.progress(run, 2, finished=True)
        self.assertEqual(save.call_count, 3)
        self.assertTrue(save.call_args.args[1]["finished"])


class AudioTests(unittest.TestCase):
    def test_codec_config_pseudo_frame_is_removed_only_from_a_temporary_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "valid.aac"
            with av.open(str(valid), "w", format="adts") as output:
                stream = output.add_stream("aac", rate=RATE)
                stream.layout = "mono"
                for _ in range(4):
                    frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
                    frame.sample_rate = RATE
                    frame.planes[0].update(bytes(frame.planes[0].buffer_size))
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode(None):
                    output.mux(packet)
            original = valid.read_bytes()
            header = bytearray(original[:7])
            frequency = (header[2] >> 2) & 15
            channels = ((header[2] & 1) << 2) | (header[3] >> 6)
            config = bytes([
                (((header[2] >> 6) + 1) << 3) | (frequency >> 1),
                ((frequency & 1) << 7) | (channels << 3),
            ])
            header[3] &= 0xFC
            header[4] = 1
            header[5] = (header[5] & 31) | 32
            broken = root / "recording.aac"
            data = bytes(header) + config + original
            broken.write_bytes(data)
            self.assertEqual(aac_config_prefix(valid), 0)
            self.assertEqual(aac_config_prefix(broken), 9)
            with audio_decode_source(broken, root) as normalized:
                self.assertEqual(normalized.read_bytes(), original)
                with av.open(str(normalized)) as incoming:
                    self.assertGreater(sum(frame.samples for frame in incoming.decode(audio=0)), 0)
            self.assertFalse(normalized.exists())
            self.assertEqual(broken.read_bytes(), data)
            broken.write_bytes(bytes(header) + b"\x00\x00" + original)
            self.assertEqual(aac_config_prefix(broken), 0)

    def test_unusable_timestamps_are_retried_on_smaller_audio_with_global_offsets(self):
        class RetimingRun:
            transcribe_segments = BulkRun.transcribe_segments

            def __init__(self, root):
                self.cache = root
                self.stop = threading.Event()
                self.calls = 0
                (root / "audio").mkdir()

            def transcribe(self, chunk):
                self.calls += 1
                duration = 0 if self.calls == 1 else chunk["seconds"] * 1000
                return str(self.calls), {"phrases": [{
                    "text": "Speech", "offsetMilliseconds": 0, "durationMilliseconds": duration,
                }]}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "audio.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(RATE)
                output.writeframes(bytes(RATE * 120 * 2))
            run = RetimingRun(root)
            for chunk in audio_chunks(source, root, run.stop):
                chunk["offset_ms"] = 5000
                with self.assertLogs(level="WARNING"):
                    segments = list(run.transcribe_segments(chunk))
            self.assertGreater(len(segments), 1)
            self.assertEqual(segments[0]["offset_ms"], 5000)
            self.assertAlmostEqual(sum(s["seconds"] for s in segments), 120)
            for previous, following in zip(segments, segments[1:]):
                self.assertAlmostEqual(
                    previous["offset_ms"] + previous["seconds"] * 1000,
                    following["offset_ms"],
                )
            self.assertTrue(all(s["timing_retry_parent"] == "1" for s in segments))

    def test_selects_only_the_unique_default_audio_track(self):
        default = SimpleNamespace(disposition=1, codec_context=object())
        alternative = SimpleNamespace(disposition=0, codec_context=None)
        container = SimpleNamespace(streams=SimpleNamespace(audio=[default, alternative]))
        self.assertIs(select_audio(container), default)
        default.disposition = 0
        with self.assertRaises(ValueError):
            select_audio(container)

    def test_chunking_preserves_pcm_and_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "audio.wav"
            pcm = array("h", ((index % 200) - 100 for index in range(RATE * 9))).tobytes()
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(RATE)
                output.writeframes(pcm)
            decoded = bytearray()
            chunks = 0
            for chunk in audio_chunks(source, root, threading.Event(), chunk_seconds=4):
                self.assertAlmostEqual(chunk["offset_ms"], len(decoded) / 2 / RATE * 1000)
                self.assertLessEqual(chunk["seconds"], 4)
                part = bytearray()
                with av.open(str(chunk["path"])) as incoming:
                    for frame in incoming.decode(audio=0):
                        part.extend(bytes(frame.planes[0])[:frame.samples * 2])
                self.assertEqual(hashlib.sha256(part).hexdigest(), chunk["pcm_sha256"])
                decoded.extend(part)
                chunks += 1
            self.assertGreater(chunks, 2)
            self.assertEqual(decoded, pcm)
            self.assertEqual(list(root.glob("part-*.flac")), [])


@unittest.skipUnless(os.name == "nt", "Windows publication semantics")
class PublicationTests(unittest.TestCase):
    def make_job(self, root):
        source = root / "20220101_010203-Place.m4a"
        source.write_bytes(b"original media")
        return {
            "source": str(source), "fingerprint": fingerprint(source),
            "title": "Summary", "phase": "titled",
        }, root / "job.json"

    def test_collision_never_overwrites_media_or_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job, state = self.make_job(root)
            occupied = root / "20220101_010203-Place-Summary.srt"
            occupied.write_text("user subtitles")
            publish_pair(job, state, "generated", threading.Lock())
            self.assertEqual(occupied.read_text(), "user subtitles")
            self.assertEqual(Path(job["destination"]).name, "20220101_010203-Place-Summary_1.m4a")
            self.assertEqual(Path(job["sidecar"]).read_text(), "generated")
            self.assertEqual(Path(job["destination"]).read_bytes(), b"original media")

    def test_prepared_repair_preserves_another_extension_companion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job, state = self.make_job(root)
            source = Path(job["source"])
            other_media = source.with_suffix(".mp4")
            other_media.write_bytes(b"other media")
            companion = source.with_suffix(".srt")
            companion.write_text("other subtitles")
            destination, sidecar = choose_pair_paths(source, target_stem(source, job["title"]))
            subtitle = "generated subtitles"
            job.update(
                destination=str(destination), sidecar=str(sidecar),
                staging=str(root / "repair.tmp"),
                subtitle_sha256=hashlib.sha256(subtitle.encode()).hexdigest(),
                phase="prepared",
            )
            publish_pair(job, state, subtitle, threading.Lock())
            self.assertEqual(other_media.read_bytes(), b"other media")
            self.assertEqual(companion.read_text(), "other subtitles")
            self.assertEqual(sidecar.read_text(), subtitle)
            self.assertEqual(destination.read_bytes(), b"original media")

    def test_resume_between_media_and_subtitle_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job, state = self.make_job(root)
            original_rename = os.rename

            def interrupt(source, destination):
                if str(source).endswith(".tmp"):
                    raise OSError("Simulated disconnect")
                return original_rename(source, destination)

            with patch("TranscribeMedia.os.rename", side_effect=interrupt):
                with self.assertRaises(OSError):
                    publish_pair(job, state, "generated", threading.Lock())
            recovered = json.loads(state.read_text())
            self.assertEqual(recovered["phase"], "media_renamed")
            publish_pair(recovered, state, "generated", threading.Lock())
            self.assertEqual(recovered["phase"], "done")
            self.assertEqual(Path(recovered["destination"]).read_bytes(), b"original media")
            self.assertEqual(Path(recovered["sidecar"]).read_text(), "generated")
            self.assertFalse(Path(recovered["staging"]).exists())

    def test_changed_sources_and_existing_source_subtitles_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job, state = self.make_job(root)
            source = Path(job["source"])
            sidecar = source.with_suffix(".srt")
            sidecar.write_text("user subtitles")
            with self.assertRaises(FileExistsError):
                publish_pair(job, state, "generated", threading.Lock())
            self.assertTrue(source.exists())
            sidecar.unlink()
            source.write_bytes(b"changed media")
            with self.assertRaises(ValueError):
                publish_pair(job, state, "generated", threading.Lock())
            self.assertEqual(source.read_bytes(), b"changed media")

    def test_media_without_speech_publishes_no_subtitle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            job, state = self.make_job(root)
            job.pop("title")
            source = Path(job["source"])
            publish_pair(job, state, "", threading.Lock())
            self.assertIsNone(job["sidecar"])
            self.assertEqual(job["phase"], "done")
            self.assertEqual(Path(job["destination"]), source)
            self.assertEqual(source.read_bytes(), b"original media")
            self.assertFalse(Path(job["staging"]).exists())
            self.assertEqual(list(root.glob("*.srt")), [])
            publish_pair(json.loads(state.read_text()), state, "", threading.Lock())
            self.assertEqual(source.read_bytes(), b"original media")
            self.assertEqual(list(root.glob("*.srt")), [])

    def test_duplicate_runner_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            with exclusive_run(Path(temp)):
                with self.assertRaises(OSError):
                    with exclusive_run(Path(temp)):
                        pass


if __name__ == "__main__":
    unittest.main()
