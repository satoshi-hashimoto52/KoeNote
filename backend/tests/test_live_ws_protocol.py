import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from services.pcm_stream import SAMPLE_RATE

FRAME_SAMPLES = 2048
LOUD_FRAME = np.full(FRAME_SAMPLES, 8000, dtype="<i2").tobytes()


def stub_result(text="あ", start=0.0, end=8.0):
    return {
        "text": text,
        "segments": [{"start": start, "end": end, "text": text}] if text else [],
        "model": "tiny",
        "rms": 0.2,
        "skipped": not bool(text),
        "skip_reason": "",
        "debug_path": None,
        "debug_wav_path": None,
    }


def base_config(**over):
    payload = {
        "type": "config",
        "send_mode": "pcm16",
        "sample_rate": SAMPLE_RATE,
        "model": "tiny",
        "chunk_seconds": 2,
        "overlap_seconds": 0.5,
        "write_to_file": False,
        "sample_rate": SAMPLE_RATE,
    }
    payload.update(over)
    return payload


def seconds_of_frames(seconds: float) -> int:
    return int(seconds * SAMPLE_RATE / FRAME_SAMPLES) + 1


class LiveWsProtocolTest(unittest.TestCase):
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

    def collect(self, ws, wanted, limit=400, timeout=15.0):
        """指定 type のメッセージが来るまで読み進める。"""
        deadline = time.time() + timeout
        seen = []
        for _ in range(limit):
            if time.time() > deadline:
                break
            message = ws.receive_json()
            seen.append(message)
            if message.get("type") in wanted:
                return message, seen
        return None, seen

    def test_pcm16_config_is_accepted_and_full_is_rejected(self):
        with self.TestClient(self.app) as client:
            with client.websocket_connect("/ws/live") as ws:
                ws.send_json(base_config())
                ready = ws.receive_json()
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["sample_rate"], SAMPLE_RATE)
                self.assertTrue(ready["session_id"])

            with client.websocket_connect("/ws/live") as ws:
                ws.send_json(base_config(send_mode="full"))
                error = ws.receive_json()
                self.assertEqual(error["type"], "error")
                self.assertIn("O(T^2)", error["message"])

    def test_heartbeat_keeps_arriving_while_inference_is_slow(self):
        """推論が詰まっていても生存信号が止まらないこと。

        これが止まると uvicorn の keepalive で切断され、旧実装の 1011 が再発する。
        """
        def slow(pcm, model, debug_save=False, sample_rate=SAMPLE_RATE):
            time.sleep(1.2)
            return stub_result()

        with patch("services.live_session.transcribe_pcm16", new=slow):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(4)):
                        ws.send_bytes(LOUD_FRAME)

                    heartbeats = []
                    deadline = time.time() + 12.0
                    while len(heartbeats) < 3 and time.time() < deadline:
                        message = ws.receive_json()
                        if message.get("type") == "heartbeat":
                            heartbeats.append(message)

        self.assertGreaterEqual(len(heartbeats), 3)
        self.assertIn("recorded_seconds", heartbeats[0])
        self.assertIn("last_audio_received_at", heartbeats[0])
        self.assertIn("lag_seconds", heartbeats[0])
        self.assertEqual([h["seq"] for h in heartbeats], sorted(h["seq"] for h in heartbeats))

    def test_update_carries_delta_only_and_never_full_text(self):
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="こんにちは", end=1.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    update, _seen = self.collect(ws, {"update"})

        self.assertIsNotNone(update, "update が届かなかった")
        self.assertNotIn("committed_text", update)
        self.assertEqual(update["committed_delta"], "こんにちは")
        self.assertEqual(update["committed_length_before"], 0)
        self.assertEqual(update["committed_length"], len("こんにちは"))
        self.assertFalse(update["needs_snapshot"])

    def test_resync_returns_a_full_snapshot(self):
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="本文", end=1.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    update, _ = self.collect(ws, {"update"})
                    self.assertIsNotNone(update)

                    ws.send_json({"type": "resync"})
                    snapshot, _ = self.collect(ws, {"snapshot"})

        self.assertIsNotNone(snapshot, "snapshot が届かなかった")
        self.assertIn("本文", snapshot["committed_text"])
        self.assertEqual(snapshot["committed_length"], len(snapshot["committed_text"]))

    def test_metrics_are_silent_unless_debug_is_enabled(self):
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="X", end=1.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config(debug=False))
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    _update, seen = self.collect(ws, {"update"})
                self.assertEqual([m for m in seen if m["type"] == "metrics"], [])
                self.assertEqual([m for m in seen if m["type"] == "log"], [])

                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config(debug=True))
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    metrics, _ = self.collect(ws, {"metrics"})

        self.assertIsNotNone(metrics, "debug 有効時は metrics が届くべき")
        self.assertIn("inference_ms", metrics)
        # セグメントのテキストは絶対に載せない（1時間で膨大な冗長データになる）。
        self.assertNotIn("segments", metrics)

    def test_stop_delivers_session_final(self):
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="終", end=1.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    self.collect(ws, {"update"})
                    ws.send_json({"type": "stop"})
                    final, _ = self.collect(ws, {"session_final"})

        self.assertIsNotNone(final, "session_final が届かなかった")
        self.assertIn("終", final["committed_text"])
        self.assertGreater(final["recorded_seconds"], 4.0)
        self.assertEqual(final["dropped_seconds"], 0.0)

    def test_gap_message_keeps_the_timeline_aligned(self):
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="", end=0.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(base_config())
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    ws.send_bytes(LOUD_FRAME)
                    ws.send_json({"type": "gap", "samples": 3 * SAMPLE_RATE})
                    for _ in range(seconds_of_frames(1)):
                        ws.send_bytes(LOUD_FRAME)
                    heartbeat, _ = self.collect(ws, {"heartbeat"})
                    # gap を無音で埋めるので、録音秒数は送ったフレームより長くなる。
                    deadline = time.time() + 8.0
                    while heartbeat["recorded_seconds"] < 3.0 and time.time() < deadline:
                        heartbeat, _ = self.collect(ws, {"heartbeat"})

        self.assertGreaterEqual(heartbeat["recorded_seconds"], 3.0)
        self.assertGreaterEqual(heartbeat["dropped_seconds"], 3.0)

    def test_reconnect_resumes_the_same_session_and_transcript(self):
        outdir = Path(tempfile.mkdtemp(prefix="bridgelog_ws_resume_"))
        config = base_config(write_to_file=True, output_folder=str(outdir), output_filename="transcript.txt")
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result(text="前半", end=1.0)):
            with self.TestClient(self.app) as client:
                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json(config)
                    ready = ws.receive_json()
                    session_id = ready["session_id"]
                    self.assertTrue(ready["audio_path"])
                    for _ in range(seconds_of_frames(6)):
                        ws.send_bytes(LOUD_FRAME)
                    self.collect(ws, {"update"})
                    # stop を送らずに切断（異常切断の再現）

                with client.websocket_connect("/ws/live") as ws:
                    ws.send_json({**config, "resume_session_id": session_id})
                    resumed = ws.receive_json()
                    self.assertEqual(resumed["type"], "resumed")
                    self.assertEqual(resumed["session_id"], session_id)
                    self.assertGreater(resumed["server_total_samples"], 0)
                    snapshot = ws.receive_json()
                    self.assertEqual(snapshot["type"], "snapshot")
                    self.assertIn("前半", snapshot["committed_text"])

        # transcript.txt と recording.wav が 1 本のまま継続していること
        self.assertEqual(len(list(outdir.glob("*.txt"))), 1)
        audio = outdir / "audio" / "recording.wav"
        self.assertTrue(audio.is_file())
        with wave.open(str(audio), "rb") as wav:
            self.assertGreater(wav.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
