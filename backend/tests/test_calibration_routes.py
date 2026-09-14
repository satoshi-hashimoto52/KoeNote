"""入力テスト（キャリブレーション）の算出と API（0019）。

判定は録音時と同じ services.audio_levels を使うため、
「テストでは良好、実際は落ちる」というずれが起きないことを守る。
"""
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services import calibration  # noqa: E402
from services.audio_levels import MAX_GAIN_DB, float32_to_pcm16  # noqa: E402
from tests.test_input_gain import (  # noqa: E402
    make_meeting_audio,
    make_room_tone,
    make_silence,
)


def b64(samples) -> str:
    return base64.b64encode(float32_to_pcm16(samples)).decode()


class AnalyzeCalibrationTest(unittest.TestCase):
    def analyze(self, speech, noise=None, label="MacBook Pro のマイク", profile=None):
        return calibration.analyze_calibration(
            float32_to_pcm16(speech),
            noise_pcm=None if noise is None else float32_to_pcm16(noise),
            device_label=label,
            profile_payload=profile,
        )

    def test_reports_dbfs_not_db_spl(self):
        result = self.analyze(make_meeting_audio(18.0), make_room_tone(5.0))
        self.assertEqual(result["unit"], "dBFS")
        for key in result:
            self.assertNotIn("db_spl", key.lower())
            self.assertNotIn("spl", key.lower())

    def test_uses_percentiles_not_extremes(self):
        """瞬間ノイズ 1 発で判定が変わらないこと。"""
        speech = make_meeting_audio(18.0)
        base = self.analyze(speech, make_room_tone(5.0))
        spiked = speech.copy()
        spiked[9999] = 0.9
        after = self.analyze(spiked, make_room_tone(5.0))
        self.assertEqual(base["verdict"], after["verdict"])
        self.assertAlmostEqual(base["speech_median_dbfs"], after["speech_median_dbfs"], delta=0.5)

    def test_reports_every_required_field(self):
        result = self.analyze(make_meeting_audio(18.0), make_room_tone(5.0))
        for key in (
            "device_label", "input_mode", "detected_mode",
            "noise_floor_dbfs", "speech_median_dbfs", "speech_low_dbfs", "speech_peak_dbfs",
            "snr_db", "current_pass_ratio", "current_skip_ratio",
            "recommended_gain_db", "clipping_risk", "verdict", "recommended_settings",
        ):
            self.assertIn(key, result, key)

    def test_noise_step_sets_the_floor_for_the_speech_step(self):
        quiet_room = make_room_tone(5.0, noise_dbfs=-70.0)
        loud_room = make_room_tone(5.0, noise_dbfs=-45.0)
        speech = make_meeting_audio(18.0)
        with_quiet = self.analyze(speech, quiet_room)
        with_loud = self.analyze(speech, loud_room)
        self.assertGreater(with_quiet["snr_db"], with_loud["snr_db"])

    def test_works_without_the_noise_step(self):
        result = self.analyze(make_meeting_audio(18.0))
        self.assertIsNone(result["noise"])
        self.assertIn(result["verdict"], ("good", "usable", "needs_adjust"))

    def test_issue_0019_audio_is_usable_and_recommends_gain(self):
        result = self.analyze(make_meeting_audio(18.0), make_room_tone(5.0))
        self.assertEqual(result["verdict"], "usable")
        self.assertGreater(result["recommended_gain_db"], 6.0)
        self.assertLessEqual(result["recommended_gain_db"], MAX_GAIN_DB)
        self.assertGreater(result["recommended_pass_ratio"], result["current_pass_ratio"] - 1e-9)

    def test_no_input_is_reported(self):
        result = self.analyze(make_silence(18.0), make_silence(5.0))
        self.assertEqual(result["verdict"], "no_input")

    def test_blackhole_is_detected_and_recommended_without_gain(self):
        result = self.analyze(make_meeting_audio(18.0, speech_dbfs=-20.0),
                              label="BlackHole 2ch")
        self.assertEqual(result["input_mode"], "loopback")
        self.assertEqual(result["detected_mode"], "loopback")
        self.assertEqual(result["recommended_settings"]["gainMode"], "none")

    def test_clipping_risk_is_reported(self):
        loud = make_meeting_audio(18.0, speech_dbfs=-2.0, noise_dbfs=-40.0)
        result = self.analyze(loud, make_room_tone(5.0, noise_dbfs=-40.0))
        self.assertIn(result["clipping_risk"], ("low", "medium", "high"))

    def test_recommended_settings_stay_within_limits(self):
        result = self.analyze(make_meeting_audio(18.0, speech_dbfs=-70.0, noise_dbfs=-100.0))
        settings = result["recommended_settings"]
        self.assertLessEqual(settings["maxGainDb"], MAX_GAIN_DB)
        self.assertGreaterEqual(settings["maxGainDb"], 0.0)


class AudioRoutesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_presets_endpoint(self):
        response = self.client.get("/api/audio/presets")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["unit"], "dBFS")
        self.assertIn("mic", payload["presets"])
        self.assertIn("loopback", payload["presets"])
        self.assertEqual(payload["presets"]["loopback"]["gain_mode"], "none")
        self.assertIn("test_sentence", payload["calibration"])
        self.assertIn("KoeNote", payload["calibration"]["test_sentence"])

    def test_detect_mode_endpoint(self):
        self.assertEqual(
            self.client.get("/api/audio/detect_mode",
                            params={"device_label": "BlackHole 2ch"}).json()["detected_mode"],
            "loopback",
        )
        self.assertEqual(
            self.client.get("/api/audio/detect_mode",
                            params={"device_label": "内蔵マイク"}).json()["detected_mode"],
            "mic",
        )

    def test_analyze_endpoint(self):
        response = self.client.post("/api/audio/analyze", json={
            "speech_pcm": b64(make_meeting_audio(18.0)),
            "noise_pcm": b64(make_room_tone(5.0)),
            "device_label": "MacBook Pro のマイク",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "usable")

    def test_analyze_rejects_empty_speech(self):
        response = self.client.post("/api/audio/analyze", json={"speech_pcm": ""})
        self.assertEqual(response.status_code, 400)

    def test_analyze_rejects_broken_base64(self):
        response = self.client.post("/api/audio/analyze", json={"speech_pcm": "@@@@"})
        self.assertEqual(response.status_code, 400)

    def test_analyze_rejects_oversized_payload(self):
        oversized = base64.b64encode(b"\x00" * (calibration_max() + 2)).decode()
        response = self.client.post("/api/audio/analyze", json={"speech_pcm": oversized})
        self.assertEqual(response.status_code, 400)

    def test_analyze_rejects_out_of_range_sample_rate(self):
        response = self.client.post("/api/audio/analyze", json={
            "speech_pcm": b64(make_meeting_audio(2.0)),
            "sample_rate": 10,
        })
        self.assertEqual(response.status_code, 400)

    def test_analyze_does_not_create_a_session_folder(self):
        """入力テストが会議セッションを作らないこと。"""
        from services.live_registry import registry

        before = len(getattr(registry, "_sessions", {}))
        self.client.post("/api/audio/analyze", json={
            "speech_pcm": b64(make_meeting_audio(5.0)),
        })
        self.assertEqual(len(getattr(registry, "_sessions", {})), before)


def calibration_max() -> int:
    from routes.audio import MAX_PCM_BYTES

    return MAX_PCM_BYTES


if __name__ == "__main__":
    unittest.main()
