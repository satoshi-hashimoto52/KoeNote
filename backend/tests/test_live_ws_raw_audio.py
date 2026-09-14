"""WS 経路の不変条件: recording.wav は無補正・segments は実際に書かれる（0019）。

`test_live_session_levels.py` が LiveSession 単体を守るのに対し、
ここは routes/whisper.py まで含めた実際の受信経路を守る。
"""
import json
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.audio_levels import db_from_amplitude, float32_to_pcm16, rms  # noqa: E402
from services.pcm_stream import SAMPLE_RATE  # noqa: E402
from tests.test_input_gain import make_meeting_audio  # noqa: E402

FRAME_SAMPLES = 2048


def stub_result(text="あ", start=0.0, end=1.5, **over):
    payload = {
        "text": text,
        "segments": [{
            "start": start, "end": end, "text": text,
            "words": [{"start": start, "end": end, "text": text}],
            "avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.2,
        }] if text else [],
        "model": "tiny",
        "rms": 0.2,
        "skipped": not bool(text),
        "skip_reason": "",
        "repetitive_dropped": 0,
        "quality": {"avg_logprob": -0.3, "no_speech_prob": 0.05, "compression_ratio": 1.2},
        "no_speech": not bool(text),
        "level_skipped": False,
        "debug_path": None,
        "debug_wav_path": None,
    }
    payload.update(over)
    return payload


def base_config(**over):
    payload = {
        "type": "config",
        "send_mode": "pcm16",
        "sample_rate": SAMPLE_RATE,
        "model": "tiny",
        "chunk_seconds": 2,
        "overlap_seconds": 0.5,
        "write_to_file": False,
        "device_label": "MacBook Pro のマイク",
    }
    payload.update(over)
    return payload


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


class RawRecordingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main

        cls.TestClient = TestClient
        cls.app = main.app

    def setUp(self):
        from services.live_registry import registry

        for entry in list(registry._entries.values()):
            entry.session.close()
        registry._entries.clear()

    def _run_session(self, tmp: str, config_over: dict, audio: np.ndarray):
        pcm = float32_to_pcm16(audio)
        frames = [pcm[i : i + FRAME_SAMPLES * 2] for i in range(0, len(pcm), FRAME_SAMPLES * 2)]
        with patch("services.live_session.transcribe_pcm16", new=lambda *a, **k: stub_result()):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config(
                        write_to_file=True,
                        output_folder=tmp,
                        output_filename="transcript.txt",
                        **config_over,
                    ))
                    ready = ws.receive_json()
                    self.assertEqual(ready["type"], "ready")
                    audio_path = ready["audio_path"]
                    self.assertTrue(audio_path)
                    for frame in frames:
                        ws.send_bytes(frame)
                    time.sleep(0.6)
                    ws.send_json({"type": "stop"})
                    deadline = time.time() + 15
                    while time.time() < deadline:
                        message = ws.receive_json()
                        if message.get("type") == "session_final":
                            break
        return Path(audio_path), pcm

    def test_recording_wav_holds_the_uncorrected_audio(self):
        """補正は解析経路だけ。recording.wav は原本のまま残す。"""
        audio = make_meeting_audio(6.0)
        with tempfile.TemporaryDirectory() as tmp:
            audio_path, sent = self._run_session(tmp, {}, audio)
            stored = audio_path.read_bytes()[44:]  # RIFF ヘッダを除く
            self.assertEqual(
                stored, sent,
                "recording.wav が補正後の音声になっている（原本が失われる）",
            )
            recorded = read_wav(audio_path)
            self.assertAlmostEqual(
                db_from_amplitude(rms(recorded)),
                db_from_amplitude(rms(audio)),
                delta=0.5,
            )

    def test_ready_reports_the_resolved_input_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("services.live_session.transcribe_pcm16", new=lambda *a, **k: stub_result()):
                with self.TestClient(self.app) as client:
                    with client.websocket_connect("/ws/live") as ws:
                        ws.send_json(base_config(
                            write_to_file=True, output_folder=tmp,
                            output_filename="transcript.txt",
                            device_label="BlackHole 2ch",
                        ))
                        ready = ws.receive_json()
                        profile = ready["input_profile"]
                        self.assertEqual(profile["mode"], "loopback")
                        self.assertEqual(profile["detected_mode"], "loopback")
                        self.assertFalse(profile["gain_enabled"])

    def test_manual_mode_overrides_detection_over_the_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("services.live_session.transcribe_pcm16", new=lambda *a, **k: stub_result()):
                with self.TestClient(self.app) as client:
                    with client.websocket_connect("/ws/live") as ws:
                        ws.send_json(base_config(
                            write_to_file=True, output_folder=tmp,
                            output_filename="transcript.txt",
                            device_label="BlackHole 2ch",
                            input_profile={"mode": "mic"},
                        ))
                        profile = ws.receive_json()["input_profile"]
                        self.assertEqual(profile["mode"], "mic")
                        self.assertEqual(profile["detected_mode"], "loopback")

    def test_segments_json_is_written(self):
        """session.json の宣言どおり transcript_segments.json が生成されること。"""
        audio = make_meeting_audio(6.0)
        with tempfile.TemporaryDirectory() as tmp:
            self._run_session(tmp, {}, audio)
            target = Path(tmp) / "transcript_segments.json"
            self.assertTrue(target.is_file(), "transcript_segments.json が生成されていない")
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertGreater(payload["segment_count"], 0)
            record = payload["segments"][0]
            for key in ("start", "end", "text", "state"):
                self.assertIn(key, record)
            self.assertEqual(record["state"], "committed")

    def test_diagnostics_records_the_input_profile(self):
        audio = make_meeting_audio(6.0)
        with tempfile.TemporaryDirectory() as tmp:
            self._run_session(tmp, {}, audio)
            log = Path(tmp) / "diagnostics.log"
            self.assertTrue(log.is_file(), "diagnostics.log が生成されていない")
            text = log.read_text(encoding="utf-8")
            self.assertIn("input_profile", text)
            self.assertIn("mode=mic", text)
            self.assertIn("gain_mode=auto", text)

    def test_heartbeat_carries_input_levels(self):
        audio = make_meeting_audio(4.0)
        pcm = float32_to_pcm16(audio)
        with patch("services.live_session.transcribe_pcm16", new=lambda *a, **k: stub_result()):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    ws.receive_json()
                    for i in range(0, len(pcm), FRAME_SAMPLES * 2):
                        ws.send_bytes(pcm[i : i + FRAME_SAMPLES * 2])
                    deadline = time.time() + 15
                    levels = None
                    while time.time() < deadline:
                        message = ws.receive_json()
                        if message.get("type") == "heartbeat":
                            levels = message.get("input_levels")
                            break
                    self.assertIsNotNone(levels, "heartbeat に input_levels が無い")
                    for key in ("input_mode", "gain_db", "noise_floor_dbfs",
                                "level_skipped_seconds", "vad_silence_seconds", "level_state"):
                        self.assertIn(key, levels, key)


if __name__ == "__main__":
    unittest.main()
