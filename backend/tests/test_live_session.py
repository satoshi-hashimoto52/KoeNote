import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.live_session import LiveSession, LiveSessionConfig


class LiveSessionTest(unittest.TestCase):
    def test_text_is_kept_as_partial_when_segment_timestamps_are_missing(self):
        config = LiveSessionConfig(model="tiny", chunk_seconds=5, overlap_seconds=1, write_to_file=False, send_mode="full")
        session = LiveSession(config)
        result = {
            "text": "認識結果",
            "model": "tiny",
            "rms": 0.1,
            "skipped": False,
            "skip_reason": "",
            "segments": [],
            "debug_wav_path": None,
        }
        with patch("services.live_session.convert_webm_bytes_to_wav", return_value={"wav_bytes": b"wav", "debug_path": None, "debug_wav_path": None}), \
                patch.object(session, "_wav_duration_seconds", return_value=5.0), \
                patch.object(session, "_extract_tail_wav"), \
                patch("services.live_session.transcribe_wav_file", return_value=result):
            payload = session.transcribe_chunk(b"window")

        self.assertEqual(payload["committed_append"], "")
        self.assertEqual(payload["partial_text"], "認識結果")
        self.assertTrue(payload["classification_warning"])

    def test_full_windows_do_not_accumulate_or_wait_for_duration_growth(self):
        config = LiveSessionConfig(
            model="tiny",
            chunk_seconds=5,
            overlap_seconds=1,
            write_to_file=False,
            mime_type="audio/webm",
            send_mode="full",
        )
        session = LiveSession(config)
        wav_result = {"wav_bytes": b"wav", "debug_path": None, "debug_wav_path": None}
        chunk_result = {
            "text": "同じ文",
            "model": "tiny",
            "rms": 0.1,
            "skipped": False,
            "skip_reason": "",
            "debug_wav_path": None,
        }
        results = [
            {**chunk_result, "text": "A B C", "segments": [{"start": 0, "end": 3, "text": "A"}, {"start": 3, "end": 5, "text": "B C"}]},
            {**chunk_result, "text": "B C D", "segments": [{"start": 0, "end": 2, "text": "B C"}, {"start": 2, "end": 5, "text": "D"}]},
        ]
        with patch("services.live_session.convert_webm_bytes_to_wav", return_value=wav_result), \
                patch.object(session, "_wav_duration_seconds", side_effect=[5.0, 9.0]), \
                patch.object(session, "_extract_tail_wav"), \
                patch("services.live_session.transcribe_wav_file", side_effect=results) as transcribe:
            first = session.transcribe_chunk(b"window-1")
            second = session.transcribe_chunk(b"window-2")

        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(len(session.raw_chunks), 1)
        self.assertEqual(first["final"], "A")
        self.assertEqual(second["final"], "B C")
        self.assertEqual(session.committed_text, "AB C")
        self.assertEqual(second["committed_text"], "AB C")
        self.assertEqual(session.partial_text, "D")
        self.assertGreater(second["window_end"], first["window_end"])
        self.assertEqual(session.finalize()["committed_text"], "AB CD")
        self.assertIsNotNone(session.last_audio_received_at)
        self.assertIsNotNone(session.last_transcription_at)


if __name__ == "__main__":
    unittest.main()
