"""入力音量の自動補正・無音判定・低音量検知の回帰テスト（0019）。

実音声そのものは**リポジトリへ入れない**（機密情報を含みうる）。
代わりに 0019 で実測した特徴だけを再現した合成 fixture を使う。

    ノイズフロア -62 dBFS / 発話レベル -38 dBFS / SNR 24 dB
    10 秒窓の平均 RMS は -48 dBFS 付近（= 旧 MIN_RMS 0.006 を下回る）

このテストが守る一線:
    固定 MIN_RMS のような「窓平均 RMS の絶対比較」で低音量の発話が
    大量破棄される回帰を、二度と通さないこと。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.audio_levels import (  # noqa: E402
    ABSOLUTE_SILENCE_RMS,
    CEILING,
    LEVEL_FRAME_SAMPLES,
    MAX_GAIN_DB,
    TARGET_SPEECH_DBFS,
    NoiseFloorTracker,
    amplitude_from_db,
    analyze_levels,
    classify_verdict,
    db_from_amplitude,
    estimate_pass_ratio,
    float32_to_pcm16,
    frame_rms,
    pcm16_to_float32,
    recommended_gain_db,
    required_gain_db,
    rms,
)
from services.input_gain import AdaptiveGain  # noqa: E402
from services.input_profile import (  # noqa: E402
    GAIN_AUTO,
    GAIN_NONE,
    MODE_CUSTOM,
    MODE_LOOPBACK,
    MODE_MIC,
    SILENCE_ABSOLUTE,
    SILENCE_RELATIVE,
    build_profile,
    detect_mode,
)

SR = 16000

# 0019 実測値。fixture の目標。
FIXTURE_NOISE_DBFS = -62.0
FIXTURE_SPEECH_DBFS = -38.0
# 旧実装の固定しきい値。撤去されたことをテストで固定する。
LEGACY_MIN_RMS = 0.006


def make_meeting_audio(seconds: float = 30.0, seed: int = 7,
                       speech_dbfs: float = FIXTURE_SPEECH_DBFS,
                       noise_dbfs: float = FIXTURE_NOISE_DBFS) -> np.ndarray:
    """遠方会議音声を模した合成波形。

    発話 0.5 秒 / 休止 3.5 秒を繰り返す。休止を含むため 10 秒窓の平均 RMS は
    発話レベルより約 9 dB 低く出る。0019 の実測（発話 -38 dBFS に対し
    窓平均 -48 dBFS）と同じ関係であり、旧 MIN_RMS 0.006 を下回る条件になる。
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    # 発話区間のエンベロープ
    cycle = 4.0
    phase = np.mod(t, cycle)
    env = ((phase >= 0.0) & (phase < 0.5)).astype(np.float32)
    # 声帯風の倍音（単一正弦より現実の RMS/ピーク比に近づく）
    voice = np.zeros(n, dtype=np.float32)
    for harmonic, weight in ((140.0, 1.0), (280.0, 0.5), (420.0, 0.3), (700.0, 0.15)):
        voice += (weight * np.sin(2 * np.pi * harmonic * t)).astype(np.float32)
    voice /= float(np.sqrt(np.mean(np.square(voice))))
    speech = voice * amplitude_from_db(speech_dbfs) * env
    noise = rng.normal(0.0, amplitude_from_db(noise_dbfs), n).astype(np.float32)
    return (speech + noise).astype(np.float32)


def make_silence(seconds: float = 10.0) -> np.ndarray:
    """厳密なデジタル無音（BlackHole の無音と同じ）。"""
    return np.zeros(int(seconds * SR), dtype=np.float32)


def make_room_tone(seconds: float = 10.0, seed: int = 3,
                   noise_dbfs: float = FIXTURE_NOISE_DBFS) -> np.ndarray:
    """発話のない環境音のみ。"""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    return rng.normal(0.0, amplitude_from_db(noise_dbfs), n).astype(np.float32)


def run_gain(profile, samples: np.ndarray, chunk: int = LEVEL_FRAME_SAMPLES):
    """AdaptiveGain へフロントと同じ 2048 サンプル単位で流し込む。"""
    gain = AdaptiveGain(profile)
    pcm = float32_to_pcm16(samples)
    out = bytearray()
    levels = []
    step = chunk * 2  # bytes
    for i in range(0, len(pcm), step):
        chunk_out, chunk_levels = gain.process(pcm[i : i + step])
        out += chunk_out
        levels += chunk_levels
    return gain, bytes(out), levels


class FixtureSanityTest(unittest.TestCase):
    """合成 fixture が 0019 の実測特徴を再現していること。"""

    def test_fixture_matches_measured_characteristics(self):
        audio = make_meeting_audio(30.0)
        stats = analyze_levels(audio)
        self.assertAlmostEqual(db_from_amplitude(stats.noise_floor), FIXTURE_NOISE_DBFS, delta=4.0)
        self.assertAlmostEqual(db_from_amplitude(stats.speech_median), FIXTURE_SPEECH_DBFS, delta=3.0)
        self.assertGreater(stats.snr_db, 18.0)

    def test_window_mean_rms_falls_below_the_legacy_threshold(self):
        """10 秒窓の平均 RMS が旧 MIN_RMS を下回る = 0019 の再現条件。"""
        audio = make_meeting_audio(30.0)
        window = 10 * SR
        means = [rms(audio[i : i + window]) for i in range(0, len(audio) - window + 1, window)]
        self.assertTrue(means, "窓が作れていない")
        for value in means:
            self.assertLess(
                value, LEGACY_MIN_RMS,
                f"fixture の窓平均 RMS {value:.5f} が旧しきい値以上。0019 を再現できていない",
            )


class LegacyGateRegressionTest(unittest.TestCase):
    """固定 MIN_RMS による大量破棄が再発しないこと。"""

    def test_min_rms_constant_is_gone(self):
        import services.live_transcriber as lt

        self.assertFalse(
            hasattr(lt, "MIN_RMS"),
            "固定 MIN_RMS が復活している。0019 の原因そのもの",
        )

    def test_absolute_floor_is_far_below_the_measured_noise_floor(self):
        """絶対下限は遠方発話どころかノイズフロアより十分低いこと。"""
        self.assertLess(ABSOLUTE_SILENCE_RMS, amplitude_from_db(FIXTURE_NOISE_DBFS) / 2.0)
        self.assertLess(ABSOLUTE_SILENCE_RMS, LEGACY_MIN_RMS / 10.0)

    def test_speech_around_0037_rms_is_processed_not_skipped(self):
        """RMS 0.0037 前後（0019 の窓平均）の入力が処理対象になること。"""
        audio = make_meeting_audio(30.0)
        window = 10 * SR
        segment = audio[:window]
        self.assertLess(rms(segment), LEGACY_MIN_RMS)
        self.assertGreater(rms(segment), ABSOLUTE_SILENCE_RMS)

        profile = build_profile({"mode": MODE_MIC}, "MacBook Pro のマイク")
        gain, _out, levels = run_gain(profile, segment)
        self.assertTrue(any(level.is_speech for level in levels),
                        "発話フレームが 1 つも検出されていない")

    def test_frame_based_gate_keeps_almost_every_window(self):
        """窓平均ではなくフレーム基準で判定すれば、ほぼ全窓が残ること。"""
        audio = make_meeting_audio(60.0)
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, _out, levels = run_gain(profile, audio)

        frames_per_window = int(10 * SR / LEVEL_FRAME_SAMPLES)
        windows = [levels[i : i + frames_per_window]
                   for i in range(0, len(levels) - frames_per_window + 1, frames_per_window)]
        self.assertTrue(windows)
        skipped = sum(1 for w in windows if not any(f.is_speech for f in w))
        ratio = skipped / len(windows)
        self.assertLess(ratio, 0.10,
                        f"スキップ率 {ratio:.1%} が高すぎる（旧実装は 79%）")

        # 参考: 同じ音声を旧方式（窓平均 RMS）で判定すると大量に落ちる
        legacy_skipped = sum(
            1 for i in range(0, len(audio) - 10 * SR + 1, 10 * SR)
            if rms(audio[i : i + 10 * SR]) < LEGACY_MIN_RMS
        )
        self.assertGreater(legacy_skipped, 0, "旧方式で落ちない fixture では回帰を守れない")


class SilenceHandlingTest(unittest.TestCase):
    def test_digital_silence_yields_no_speech_frames(self):
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, _out, levels = run_gain(profile, make_silence(5.0))
        self.assertTrue(levels)
        self.assertFalse(any(level.is_speech for level in levels))

    def test_room_tone_only_is_not_amplified_to_speech_level(self):
        """環境音だけの区間を発話レベルまで持ち上げないこと。"""
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, out, levels = run_gain(profile, make_room_tone(20.0))
        corrected = pcm16_to_float32(out)
        # 目標発話レベル(-24dBFS)より十分低いままであること
        self.assertLess(
            db_from_amplitude(rms(corrected)), TARGET_SPEECH_DBFS - 10.0,
            "環境音が発話レベル付近まで増幅されている",
        )
        self.assertFalse(any(level.is_speech for level in levels))

    def test_gain_is_held_not_raised_during_silence(self):
        """無音が続いてもゲインが上がり続けないこと（ノイズの持ち上げ防止）。"""
        audio = np.concatenate([make_meeting_audio(20.0), make_silence(20.0)])
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, _out, levels = run_gain(profile, audio)
        speech_end = int(20 * SR / LEVEL_FRAME_SAMPLES)
        during_speech = max(level.gain for level in levels[:speech_end])
        during_silence = max(level.gain for level in levels[speech_end:])
        self.assertLessEqual(
            during_silence, during_speech + 1e-6,
            "無音区間でゲインが上昇している",
        )


class NoiseFloorTrackerTest(unittest.TestCase):
    def test_tracks_close_to_the_true_floor(self):
        tracker = NoiseFloorTracker()
        audio = make_meeting_audio(60.0)
        frames = frame_rms(audio)
        values = [tracker.push(float(v)) for v in frames]
        settled = values[len(values) // 2 :]
        estimate = db_from_amplitude(float(np.median(settled)))
        # 非対称追従なので真値よりやや高く出る。発話レベルは必ず下回ること。
        self.assertLess(estimate, FIXTURE_SPEECH_DBFS - 5.0)
        self.assertGreater(estimate, FIXTURE_NOISE_DBFS - 10.0)

    def test_does_not_latch_to_the_minimum(self):
        """一度の静寂で沈み込んだまま戻らない実装を弾く。"""
        tracker = NoiseFloorTracker()
        for _ in range(200):
            tracker.push(0.01)
        tracker.push(0.0)
        after = tracker.push(0.01)
        self.assertGreater(after, 0.001, "最小値へ張り付いている")


class MicPresetTest(unittest.TestCase):
    def test_auto_gain_brings_speech_near_the_target(self):
        audio = make_meeting_audio(60.0)
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, out, levels = run_gain(profile, audio)
        speech = [level.corrected_rms for level in levels if level.is_speech]
        self.assertTrue(speech)
        # 後半（追従が落ち着いたあと）で評価する
        settled = speech[len(speech) // 2 :]
        achieved = db_from_amplitude(float(np.median(settled)))
        self.assertAlmostEqual(achieved, TARGET_SPEECH_DBFS, delta=6.0)

    def test_output_sample_count_always_matches_input(self):
        """サンプル数が変わると server_total_samples がずれ、絶対時刻が壊れる。"""
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        pcm = float32_to_pcm16(make_meeting_audio(7.5))
        for chunk_bytes in (2, 64, 2048, 4096, 4097, 100003):
            gain = AdaptiveGain(profile)
            out = bytearray()
            for i in range(0, len(pcm), chunk_bytes):
                produced, _ = gain.process(pcm[i : i + chunk_bytes])
                out += produced
            self.assertEqual(len(out), len(pcm), f"chunk={chunk_bytes} でサンプル数が変わった")

    def test_max_gain_is_respected(self):
        """極端に小さい入力でも最大ゲインを超えないこと。"""
        audio = make_meeting_audio(60.0, speech_dbfs=-70.0, noise_dbfs=-95.0)
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, _out, levels = run_gain(profile, audio)
        peak_gain_db = max(db_from_amplitude(level.gain) for level in levels)
        self.assertLessEqual(peak_gain_db, MAX_GAIN_DB + 1e-6)

    def test_clipping_is_prevented_on_loud_input(self):
        """近距離の大きい音声でも補正後に音割れしないこと。"""
        audio = make_meeting_audio(30.0, speech_dbfs=-6.0, noise_dbfs=-50.0)
        # 打撃音のような単発ピークを混ぜる
        audio[int(5 * SR)] = 0.99
        audio[int(12 * SR) : int(12 * SR) + 40] = 0.98
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, out, _levels = run_gain(profile, audio)
        corrected = pcm16_to_float32(out)
        self.assertLessEqual(float(np.abs(corrected).max()), CEILING + 1e-3)
        self.assertEqual(int(np.count_nonzero(np.abs(corrected) >= 0.999)), 0)

    def test_no_pumping_gain_changes_slowly(self):
        """ゲインが 1 フレームで跳ねないこと（ポンピング防止）。"""
        audio = np.concatenate([
            make_meeting_audio(15.0, speech_dbfs=-45.0),
            make_meeting_audio(15.0, speech_dbfs=-20.0, seed=9),
        ])
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        _gain, _out, levels = run_gain(profile, audio)
        gains_db = [db_from_amplitude(level.gain) for level in levels]
        steps = [abs(b - a) for a, b in zip(gains_db, gains_db[1:])]
        self.assertLess(max(steps), 6.0, "1 フレームでのゲイン変化が大きすぎる")


class LoopbackPresetTest(unittest.TestCase):
    def test_blackhole_is_detected_as_loopback(self):
        for label in ("BlackHole 2ch", "blackhole 16ch", "Soundflower (2ch)",
                      "Loopback Audio", "VB-Cable", "Existential Audio BlackHole"):
            self.assertEqual(detect_mode(label), MODE_LOOPBACK, label)

    def test_physical_microphones_are_detected_as_mic(self):
        for label in ("MacBook Pro のマイク", "MacBook Pro Microphone", "外部マイク",
                      "USB Audio Device", "AirPods Pro", ""):
            self.assertEqual(detect_mode(label), MODE_MIC, label)

    def test_loopback_preset_does_not_touch_the_audio(self):
        profile = build_profile({"mode": "auto"}, "BlackHole 2ch")
        self.assertEqual(profile.mode, MODE_LOOPBACK)
        self.assertEqual(profile.gain_mode, GAIN_NONE)
        self.assertEqual(profile.silence_mode, SILENCE_ABSOLUTE)
        self.assertFalse(profile.gain_enabled)

        audio = make_meeting_audio(20.0, speech_dbfs=-20.0)
        pcm = float32_to_pcm16(audio)
        _gain, out, _levels = run_gain(profile, audio)
        self.assertEqual(out, pcm, "loopback の音声が書き換えられている")

    def test_loopback_uses_absolute_silence_only(self):
        """デジタル無音でノイズフロアが 0 に張り付いても判定が壊れないこと。"""
        profile = build_profile({"mode": "auto"}, "BlackHole 2ch")
        audio = np.concatenate([make_silence(5.0), make_meeting_audio(10.0, speech_dbfs=-20.0)])
        gain, _out, levels = run_gain(profile, audio)
        self.assertEqual(gain.speech_threshold(), ABSOLUTE_SILENCE_RMS)
        silent_frames = int(5 * SR / LEVEL_FRAME_SAMPLES) - 1
        self.assertFalse(any(level.is_speech for level in levels[:silent_frames]))
        self.assertTrue(any(level.is_speech for level in levels[silent_frames:]))


class CustomPresetTest(unittest.TestCase):
    def test_manual_gain_is_applied_exactly(self):
        profile = build_profile(
            {"mode": MODE_CUSTOM, "gain_mode": "manual", "manual_gain_db": 12.0},
            "内蔵マイク",
        )
        audio = make_meeting_audio(10.0, speech_dbfs=-50.0, noise_dbfs=-80.0)
        _gain, out, _levels = run_gain(profile, audio)
        corrected = pcm16_to_float32(out)
        delta = db_from_amplitude(rms(corrected)) - db_from_amplitude(rms(audio))
        self.assertAlmostEqual(delta, 12.0, delta=1.0)

    def test_out_of_range_values_fall_back_safely(self):
        profile = build_profile(
            {
                "mode": MODE_CUSTOM,
                "gain_mode": "nonsense",
                "manual_gain_db": 999.0,
                "max_gain_db": -50.0,
                "silence_mode": "???",
                "manual_silence_rms": 5.0,
                "low_input_warning_seconds": 9999,
            },
            "内蔵マイク",
        )
        self.assertEqual(profile.gain_mode, GAIN_AUTO)
        self.assertEqual(profile.silence_mode, SILENCE_RELATIVE)
        self.assertLessEqual(profile.manual_gain_db, MAX_GAIN_DB)
        self.assertGreaterEqual(profile.max_gain_db, 0.0)
        self.assertLessEqual(profile.manual_silence_rms, 0.05)
        self.assertLessEqual(profile.low_input_warning_seconds, 120.0)

    def test_clip_protection_cannot_be_disabled(self):
        profile = build_profile({"mode": MODE_CUSTOM, "clip_protection": False}, "内蔵マイク")
        self.assertTrue(profile.clip_protection)

    def test_switching_mode_does_not_keep_previous_mode_values(self):
        """モードを選び直したら、そのモードのプリセットが効くこと。"""
        profile = build_profile(
            {"mode": MODE_LOOPBACK, "gain_mode": "manual", "manual_gain_db": 20.0},
            "内蔵マイク",
        )
        self.assertEqual(profile.gain_mode, GAIN_NONE)
        self.assertEqual(profile.manual_gain_db, 0.0)


class LevelStatsTest(unittest.TestCase):
    def test_percentiles_not_extremes(self):
        """瞬間ノイズ 1 発で判定が変わらないこと。"""
        audio = make_meeting_audio(30.0)
        baseline = analyze_levels(audio)
        spiked = audio.copy()
        spiked[12345] = 0.95
        after = analyze_levels(spiked)
        self.assertAlmostEqual(
            db_from_amplitude(baseline.speech_median),
            db_from_amplitude(after.speech_median),
            delta=0.5,
        )
        # ピークだけは当然変わる（最大値で判定してはいけない根拠）
        self.assertGreater(after.peak, baseline.peak)

    def test_snr_matches_the_fixture(self):
        stats = analyze_levels(make_meeting_audio(30.0))
        self.assertGreater(stats.snr_db, 18.0)
        self.assertLess(stats.snr_db, 32.0)

    def test_recommended_gain_reaches_the_target(self):
        stats = analyze_levels(make_meeting_audio(30.0))
        gain_db = recommended_gain_db(stats)
        achieved = db_from_amplitude(stats.speech_median) + gain_db
        self.assertAlmostEqual(achieved, TARGET_SPEECH_DBFS, delta=1.0)

    def test_required_gain_is_not_capped(self):
        stats = analyze_levels(make_meeting_audio(30.0, speech_dbfs=-66.0, noise_dbfs=-95.0))
        self.assertGreater(required_gain_db(stats), MAX_GAIN_DB)
        self.assertLessEqual(recommended_gain_db(stats), MAX_GAIN_DB)


class VerdictTest(unittest.TestCase):
    def _verdict(self, audio):
        stats = analyze_levels(audio)
        required = required_gain_db(stats)
        gain_db = recommended_gain_db(stats)
        ratio = estimate_pass_ratio(
            stats, gain_db, threshold_rms=amplitude_from_db(TARGET_SPEECH_DBFS - 12.0)
        )
        return classify_verdict(stats, pass_ratio=ratio, required_db=required)

    def test_no_input(self):
        self.assertEqual(self._verdict(make_silence(10.0)), "no_input")

    def test_too_quiet(self):
        """最大ゲインでも目標へ届かない小ささ。絶対下限は上回るので no_input ではない。"""
        self.assertEqual(
            self._verdict(make_meeting_audio(20.0, speech_dbfs=-66.0, noise_dbfs=-95.0)),
            "too_quiet",
        )

    def test_noisy(self):
        # SNR 6dB 相当
        self.assertEqual(
            self._verdict(make_meeting_audio(20.0, speech_dbfs=-38.0, noise_dbfs=-44.0)),
            "noisy",
        )

    def test_clipping(self):
        audio = make_meeting_audio(20.0, speech_dbfs=-3.0, noise_dbfs=-50.0)
        audio = np.clip(audio * 4.0, -1.0, 1.0)
        self.assertEqual(self._verdict(audio), "clipping")

    def test_issue_0019_audio_is_usable_not_good(self):
        """0019 の音声は「補正で救えるが要改善」= usable と判定されること。"""
        self.assertEqual(self._verdict(make_meeting_audio(30.0)), "usable")

    def test_close_and_clean_audio_is_good(self):
        audio = make_meeting_audio(30.0, speech_dbfs=-26.0, noise_dbfs=-70.0)
        self.assertEqual(self._verdict(audio), "good")


class TelemetryTest(unittest.TestCase):
    def test_summary_is_aggregated_not_per_frame(self):
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        gain, _out, levels = run_gain(profile, make_meeting_audio(30.0))
        summary = gain.telemetry.as_dict()
        self.assertEqual(summary["frames"], len(levels))
        self.assertIsNotNone(summary["raw_rms_dbfs"])
        self.assertIsNotNone(summary["corrected_rms_dbfs"])
        self.assertGreater(summary["corrected_rms_dbfs"], summary["raw_rms_dbfs"])
        self.assertEqual(summary["clipped_samples"], 0)

    def test_reset_clears_the_window(self):
        profile = build_profile({"mode": MODE_MIC}, "内蔵マイク")
        gain, _out, _levels = run_gain(profile, make_meeting_audio(10.0))
        gain.telemetry.reset()
        self.assertEqual(gain.telemetry.as_dict()["frames"], 0)


if __name__ == "__main__":
    unittest.main()
