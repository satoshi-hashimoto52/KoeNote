import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import transcriber


class FakeProcess:
    pid = 12345

    def __init__(self, cancelled=False):
        self.returncode = 0
        self.cancelled = cancelled
        self.alive = True

    def communicate(self, timeout=None):
        if self.cancelled and self.returncode == 0:
            raise TimeoutExpired("fake", timeout)
        self.alive = False
        return json.dumps({"text": "完了"}), ""

    def poll(self):
        return None if self.alive else self.returncode

    def terminate(self):
        self.returncode = -15
        self.alive = False

    def kill(self):
        self.returncode = -9
        self.alive = False


class OneTimeoutProcess(FakeProcess):
    def __init__(self):
        super().__init__()
        self.timed_out = False

    def communicate(self, timeout=None):
        if not self.timed_out:
            self.timed_out = True
            raise TimeoutExpired("fake", timeout)
        self.alive = False
        return json.dumps({"text": "完了"}), ""


class FailingCommunicateProcess(FakeProcess):
    def communicate(self, timeout=None):
        if self.returncode == 0:
            raise OSError("pipe failed")
        self.alive = False
        return "", ""


class NonZeroProcess(FakeProcess):
    def __init__(self):
        super().__init__()
        self.returncode = -1
        self.alive = False

    def communicate(self, timeout=None):
        return "", "worker failed"


class WhisperTranscriberTest(unittest.TestCase):
    def test_worker_diagnostics_and_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            process = FakeProcess()
            events = []
            processes = []
            with patch.object(transcriber, "resolve_model_spec", return_value="small"), patch.object(
                transcriber, "resolve_python", return_value="python3"
            ), patch.object(transcriber, "probe_duration", return_value=12.5), patch.object(
                transcriber.subprocess, "Popen", return_value=process
            ):
                result = transcriber.run_transcribe(
                    str(input_path), "", "speed", False, False,
                    process_callback=processes.append,
                    diagnostic_callback=events.append,
                )
            self.assertEqual(result["text"], "完了")
            self.assertEqual([item.pid if item is not None else None for item in processes], [12345, None])
            start = next(event for event in events if event.get("event") == "start")
            started = next(event for event in events if event.get("event") == "process_started")
            self.assertEqual(start["file_size_bytes"], 5)
            self.assertEqual(start["audio_duration_seconds"], 12.5)
            self.assertEqual(started["pid"], 12345)
            finished = [event for event in events if event.get("event") == "process_finished"]
            self.assertEqual(finished[-1]["returncode"], 0)
            self.assertTrue(any(event.get("event") == "worker_returning" for event in events))

    def test_cancel_terminates_worker_and_reports_returncode(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            process = FakeProcess(cancelled=True)
            events = []
            with patch.object(transcriber, "resolve_model_spec", return_value="small"), patch.object(
                transcriber, "resolve_python", return_value="python3"
            ), patch.object(transcriber, "probe_duration", return_value=None), patch.object(
                transcriber.subprocess, "Popen", return_value=process
            ):
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    transcriber.run_transcribe(
                        str(input_path), "", "speed", False, False,
                        diagnostic_callback=events.append,
                        cancel_callback=lambda: True,
                    )
            self.assertEqual(process.returncode, -15)
            finished = [event for event in events if event.get("event") == "process_finished"]
            self.assertEqual(finished[-1]["returncode"], -15)

    def test_rss_failure_and_diagnostic_failure_do_not_fail_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            process = OneTimeoutProcess()

            def diagnostic(event):
                if event.get("event") == "progress":
                    raise RuntimeError("diagnostic unavailable")

            with patch.object(transcriber, "resolve_model_spec", return_value="small"), patch.object(
                transcriber, "resolve_python", return_value="python3"
            ), patch.object(transcriber, "probe_duration", return_value=12.5), patch.object(
                transcriber.subprocess, "Popen", return_value=process
            ):
                result = transcriber.run_transcribe(
                    str(input_path), "", "speed", False, False,
                    diagnostic_callback=diagnostic,
                )

            self.assertEqual(result["text"], "完了")

    def test_communicate_failure_reaps_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            process = FailingCommunicateProcess()

            with patch.object(transcriber, "resolve_model_spec", return_value="small"), patch.object(
                transcriber, "resolve_python", return_value="python3"
            ), patch.object(transcriber, "probe_duration", return_value=None), patch.object(
                transcriber.subprocess, "Popen", return_value=process
            ):
                with self.assertRaisesRegex(OSError, "pipe failed"):
                    transcriber.run_transcribe(str(input_path), "", "speed", False, False)

            self.assertEqual(process.returncode, -15)

    def test_nonzero_worker_returncode_is_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            process = NonZeroProcess()

            with patch.object(transcriber, "resolve_model_spec", return_value="small"), patch.object(
                transcriber, "resolve_python", return_value="python3"
            ), patch.object(transcriber, "probe_duration", return_value=None), patch.object(
                transcriber.subprocess, "Popen", return_value=process
            ):
                with self.assertRaisesRegex(RuntimeError, "worker failed"):
                    transcriber.run_transcribe(str(input_path), "", "speed", False, False)

    def test_segment_one_releases_ffmpeg_before_segment_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.wav"
            input_path.write_bytes(b"audio")
            callbacks = []
            events = []
            ffmpeg_process = FakeProcess()

            with patch.object(transcriber.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)), patch.object(
                transcriber, "resolve_ffmpeg_dir", return_value=None
            ), patch.object(transcriber.subprocess, "Popen", return_value=ffmpeg_process), patch.object(
                transcriber, "_run_batch_worker", return_value=[
                    {"text": "segment", "raw_text": "segment", "segments": []},
                    {"text": "segment", "raw_text": "segment", "segments": []},
                ]
            ) as batch_worker:
                result = transcriber._run_segmented_transcribe(
                    str(input_path), "", "speed", False, 601.0, {}, f"test-{os.urandom(8).hex()}",
                    callbacks.append, events.append, None,
                )

            self.assertEqual(result["segment_count"], 2)
            self.assertEqual(callbacks, [ffmpeg_process, None, ffmpeg_process, None])
            self.assertEqual(batch_worker.call_count, 1)
            self.assertEqual(
                [event["segment"] for event in events if event.get("event") == "segment_completed"],
                [1, 2],
            )


if __name__ == "__main__":
    unittest.main()
