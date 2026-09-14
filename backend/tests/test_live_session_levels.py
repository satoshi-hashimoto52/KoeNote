"""LiveSession 側のレベル判定・集計・診断（0019）。

`test_input_gain.py` が補正そのものを守るのに対し、ここは
「LiveSession に組み込まれたときの振る舞い」を守る。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.audio_levels import (  # noqa: E402
    LEVEL_FRAME_SAMPLES,
    float32_to_pcm16,
    pcm16_to_float32,
    rms,
)
from services.live_session import LiveSession, LiveSessionConfig  # noqa: E402
from services.pcm_stream import SAMPLE_RATE  # noqa: E402
from tests.test_input_gain import (  # noqa: E402
    LEGACY_MIN_RMS,
    make_meeting_audio,
    make_room_tone,
    make_silence,
)


def build_session(device_label="MacBook Pro のマイク", profile=None, **config):
    payload = {
        "send_mode": "pcm16",
        "write_to_file": False,
        "model": "tiny",
        "device_label": device_label,
        **config,
    }
    if profile is not None:
        payload["input_profile"] = profile
    return LiveSession(LiveSessionConfig.from_payload(payload))


def feed(session: LiveSession, samples: np.ndarray) -> None:
    """フロントと同じ 2048 サンプル単位で流し込む。"""
    pcm = float32_to_pcm16(samples)
    step = LEVEL_FRAME_SAMPLES * 2
    for i in range(0, len(pcm), step):
        session.append_pcm(pcm[i : i + step])


class RawAudioIsUntouchedTest(unittest.TestCase):
    def test_append_pcm_does_not_change_the_sample_count(self):
        """server_total_samples が送信済みサンプル数からずれないこと。

        ずれると再接続時に誤った gap が挿入され、絶対時刻が壊れる。
        """
        session = build_session()
        audio = make_meeting_audio(12.0)
        pcm = float32_to_pcm16(audio)
        feed(session, audio)
        self.assertEqual(session.pcm.total_samples, len(pcm) // 2)

    def test_correction_applies_only_to_the_analysis_buffer(self):
        """リングバッファ（= Whisper へ渡る側）だけが補正されること。"""
        session = build_session()
        audio = make_meeting_audio(20.0)
        feed(session, audio)
        stored = session.pcm.read(0, session.pcm.total_samples)
        corrected = pcm16_to_float32(stored)
        # 補正後は元より大きくなっている
        self.assertGreater(rms(corrected), rms(audio) * 2.0)


class WindowSpeechDetectionTest(unittest.TestCase):
    def test_low_level_speech_windows_are_kept(self):
        """0019 の再現条件（窓平均 RMS < 旧 MIN_RMS）でも窓が捨てられないこと。"""
        session = build_session()
        audio = make_meeting_audio(40.0)
        self.assertLess(rms(audio[: 10 * SAMPLE_RATE]), LEGACY_MIN_RMS)
        feed(session, audio)

        window = 10 * SAMPLE_RATE
        kept = 0
        total = 0
        for start in range(0, session.pcm.total_samples - window + 1, window):
            total += 1
            if session.window_has_speech(start, start + window):
                kept += 1
        self.assertGreater(total, 0)
        self.assertGreaterEqual(kept / total, 0.9, "低レベル発話の窓が捨てられている")

    def test_true_silence_windows_are_skipped(self):
        session = build_session()
        feed(session, make_silence(30.0))
        window = 10 * SAMPLE_RATE
        results = [
            session.window_has_speech(start, start + window)
            for start in range(0, session.pcm.total_samples - window + 1, window)
        ]
        self.assertTrue(results)
        self.assertFalse(any(results), "無音の窓が発話ありと判定されている")

    def test_unmeasured_range_is_not_discarded(self):
        """測定履歴が無い範囲は捨てる側へ倒さないこと。"""
        session = build_session()
        self.assertTrue(session.window_has_speech(0, 10 * SAMPLE_RATE))


class LevelStateTest(unittest.TestCase):
    """低音量警告の「発生」条件。継続時間の扱いは frontend の責務。"""

    def test_normal_audio_is_ok(self):
        session = build_session()
        feed(session, make_meeting_audio(30.0, speech_dbfs=-30.0, noise_dbfs=-62.0))
        self.assertEqual(session.evaluate_level_state(), "ok")

    def test_true_silence_is_silent_ok_not_a_warning(self):
        """通常の無言時間を異常として扱わないこと。"""
        session = build_session()
        feed(session, make_room_tone(30.0))
        self.assertEqual(session.evaluate_level_state(), "silent_ok")

    def test_no_input_when_signal_is_below_the_absolute_floor(self):
        session = build_session()
        feed(session, make_silence(30.0))
        self.assertEqual(session.evaluate_level_state(), "no_input")

    def test_too_quiet_when_max_gain_cannot_reach_the_target(self):
        session = build_session()
        feed(session, make_meeting_audio(30.0, speech_dbfs=-66.0, noise_dbfs=-95.0))
        self.assertEqual(session.evaluate_level_state(), "too_quiet")

    def test_low_snr_when_room_noise_is_high(self):
        session = build_session()
        feed(session, make_meeting_audio(30.0, speech_dbfs=-38.0, noise_dbfs=-44.0))
        self.assertEqual(session.evaluate_level_state(), "low_snr")

    def test_clipping_when_input_is_too_hot(self):
        session = build_session()
        audio = np.clip(make_meeting_audio(30.0, speech_dbfs=-3.0, noise_dbfs=-40.0) * 6.0, -1.0, 1.0)
        feed(session, audio)
        self.assertEqual(session.evaluate_level_state(), "clipping")

    def test_state_change_is_reported_once(self):
        session = build_session()
        self.assertIsNone(session.set_level_state("ok"))
        line = session.set_level_state("too_quiet")
        self.assertIsNotNone(line)
        self.assertIn("from=ok", line)
        self.assertIn("to=too_quiet", line)
        self.assertIsNone(session.set_level_state("too_quiet"), "同じ状態で再通知している")
        self.assertIsNotNone(session.set_level_state("ok"), "解除が通知されていない")


class DiagnosticsAggregationTest(unittest.TestCase):
    def test_level_report_is_throttled(self):
        """毎チャンクではなく一定間隔でしか集計行を出さないこと。"""
        session = build_session()
        feed(session, make_meeting_audio(30.0))
        self.assertIsNone(session.maybe_report_levels(), "30 秒で集計を出している")
        feed(session, make_meeting_audio(40.0, seed=11))
        line = session.maybe_report_levels()
        self.assertIsNotNone(line, "60 秒を超えても集計が出ない")
        self.assertTrue(line.startswith("input_levels "))
        for key in ("noise_floor_dbfs", "raw_rms_dbfs", "corrected_rms_dbfs", "gain_db",
                    "corrected_peak_dbfs", "limited_frames", "level_skipped_seconds",
                    "level_skipped_ratio", "vad_silence_seconds"):
            self.assertIn(key, line, f"{key} が集計に含まれていない")

    def test_snapshot_exposes_the_three_kinds_of_lost_seconds(self):
        session = build_session()
        snapshot = session.level_snapshot()
        for key in ("level_skipped_seconds", "level_skipped_ratio", "vad_silence_seconds"):
            self.assertIn(key, snapshot)
        counters = session.counters.as_dict()
        # 意味の異なる 3 つが別々に存在すること（0019 で整理した）
        self.assertIn("level_skipped_seconds", counters)
        self.assertIn("vad_silence_seconds", counters)
        self.assertIn("untranscribed_seconds", counters)

    def test_profile_summary_line_is_loggable(self):
        session = build_session()
        summary = session.profile.summary()
        self.assertIn("input_profile", summary)
        self.assertIn("mode=mic", summary)
        self.assertIn("gain_mode=auto", summary)


class ProfileResolutionTest(unittest.TestCase):
    def test_auto_resolves_blackhole_to_loopback(self):
        session = build_session(device_label="BlackHole 2ch")
        self.assertEqual(session.profile.mode, "loopback")
        self.assertEqual(session.profile.detected_mode, "loopback")
        self.assertFalse(session.profile.gain_enabled)

    def test_manual_override_wins_over_detection(self):
        session = build_session(
            device_label="BlackHole 2ch", profile={"mode": "mic"}
        )
        self.assertEqual(session.profile.mode, "mic")
        self.assertEqual(session.profile.detected_mode, "loopback")

    def test_loopback_audio_is_not_modified(self):
        session = build_session(device_label="BlackHole 2ch")
        audio = make_meeting_audio(10.0, speech_dbfs=-20.0)
        pcm = float32_to_pcm16(audio)
        feed(session, audio)
        stored = session.pcm.read(0, session.pcm.total_samples)
        self.assertEqual(stored, pcm, "loopback の音声が補正されている")


class SegmentsIntegrationTest(unittest.TestCase):
    def test_segments_file_is_written_for_a_file_backed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = LiveSession(LiveSessionConfig.from_payload({
                "send_mode": "pcm16",
                "write_to_file": True,
                "output_folder": tmp,
                "output_filename": "transcript.txt",
                "model": "tiny",
                "device_label": "内蔵マイク",
            }))
            from services.word_commit import CommitWord

            words = [CommitWord(start=0.0, end=0.4, text="こん"),
                     CommitWord(start=0.4, end=0.9, text="にちは")]
            session._append_segment_record(words, {"quality": {"avg_logprob": -0.3}})
            session.close()

            payload = json.loads(
                (Path(tmp) / "transcript_segments.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["segment_count"], 1)
            record = payload["segments"][0]
            self.assertEqual(record["text"], "こんにちは")
            self.assertEqual(record["start"], 0.0)
            self.assertEqual(record["end"], 0.9)
            self.assertEqual(record["state"], "committed")
            self.assertEqual(record["avg_logprob"], -0.3)

    def test_no_segments_writer_without_file_output(self):
        session = build_session()
        session._append_segment_record([], {})
        session.close()
        self.assertIsNone(session.segments_path)


if __name__ == "__main__":
    unittest.main()
