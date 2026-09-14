"""temperature fallback と異常反復の扱い（0019）。

`temperature: 0` のスカラー指定は faster-whisper の fallback を無効化する。
その結果 compression_ratio_threshold を超えても再デコードされず、
「はい」の反復がそのまま確定テキストへ入った（実測 446 字 / 実会話 30 秒が消失）。
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import live_transcriber as lt  # noqa: E402


class TemperatureFallbackTest(unittest.TestCase):
    def test_temperature_is_a_sequence_not_a_scalar(self):
        value = lt.TRANSCRIBE_KWARGS["temperature"]
        self.assertIsInstance(
            value, (list, tuple),
            "temperature がスカラーだと faster-whisper の fallback が無効になる",
        )
        self.assertGreater(len(value), 1, "fallback 候補が 1 つしかない")

    def test_first_temperature_is_zero(self):
        """通常音声は従来どおり temperature=0 の beam search で決まること。"""
        self.assertEqual(lt.TRANSCRIBE_KWARGS["temperature"][0], 0.0)

    def test_temperatures_increase_monotonically(self):
        values = list(lt.TRANSCRIBE_KWARGS["temperature"])
        self.assertEqual(values, sorted(values))
        self.assertLessEqual(max(values), 1.0)

    def test_format_is_accepted_by_the_installed_faster_whisper(self):
        """インストール済みパッケージが正式に受け付ける形式であること。"""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self.skipTest("faster-whisper が未インストール")
        signature = inspect.signature(WhisperModel.transcribe)
        annotation = signature.parameters["temperature"].annotation
        text = str(annotation)
        self.assertIn("List[float]", text)
        self.assertIn("Tuple[float", text)
        # 既定値もシーケンス。スカラーは「わざわざ fallback を切る」指定にあたる。
        self.assertIsInstance(signature.parameters["temperature"].default, (list, tuple))

    def test_fallback_threshold_is_still_configured(self):
        """fallback を起こす閾値そのものが外れていないこと。"""
        self.assertEqual(
            lt.TRANSCRIBE_KWARGS["compression_ratio_threshold"],
            lt.COMPRESSION_RATIO_THRESHOLD,
        )
        self.assertIsNotNone(lt.TRANSCRIBE_KWARGS["log_prob_threshold"])


class RepetitionDetectionTest(unittest.TestCase):
    def test_detects_the_measured_hallucination(self):
        """0019 で実測した「はい」×223 回を検出できること。"""
        self.assertTrue(lt.is_repetitive_text("はい" * 223))

    def test_detects_short_ngram_repetition(self):
        for text in ("あああああああああああああああああああああああああああ",
                     "そうですね" * 12,
                     "ご視聴" * 20):
            self.assertTrue(lt.is_repetitive_text(text), text[:20])

    def test_does_not_flag_normal_japanese(self):
        normal = (
            "最終的にはやってほしいですけど、そのテーマはあらなくなるんですよね。"
            "でも最初は結局そういうのってちゃんとある程度結果でないと、"
            "預けられる方も絶対抵抗感あっても、触らなくなるってなると思うんで。"
        )
        self.assertFalse(lt.is_repetitive_text(normal))

    def test_does_not_flag_legitimate_short_repeats(self):
        """「はい、はい」のような正当な繰り返しを潰さないこと。"""
        for text in ("はい、はい。", "うんうん", "ええ、ええ、そうですね。"):
            self.assertFalse(lt.is_repetitive_text(text), text)

    def test_short_text_is_never_flagged(self):
        self.assertFalse(lt.is_repetitive_text("はいはいはい"))


class SegmentFilterTest(unittest.TestCase):
    """異常反復を確定テキストへ保存しないこと。"""

    class FakeSegment:
        def __init__(self, text, no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.5):
            self.text = text
            self.no_speech_prob = no_speech_prob
            self.avg_logprob = avg_logprob
            self.compression_ratio = compression_ratio
            self.start = 0.0
            self.end = 1.0
            self.words = []

    def test_repetitive_segment_is_dropped_with_a_reason(self):
        segments = [self.FakeSegment("はい" * 223)]
        text, accepted, dropped = lt._collect_segments(segments)
        self.assertEqual(text, "")
        self.assertEqual(accepted, [])
        self.assertTrue(any(r.startswith("repetitive_segment") for r in dropped), dropped)

    def test_high_compression_ratio_is_dropped_first(self):
        segments = [self.FakeSegment("はい" * 223, compression_ratio=51.46)]
        _text, _accepted, dropped = lt._collect_segments(segments)
        self.assertTrue(any("compression_ratio" in r for r in dropped), dropped)

    def test_normal_segment_survives(self):
        segments = [self.FakeSegment("これは通常の発話です。")]
        text, accepted, dropped = lt._collect_segments(segments)
        self.assertEqual(text, "これは通常の発話です。")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(dropped, [])

    def test_quality_metrics_are_kept_for_segments_json(self):
        segments = [self.FakeSegment("こんにちは", avg_logprob=-0.42,
                                     no_speech_prob=0.05, compression_ratio=1.2)]
        _text, accepted, _dropped = lt._collect_segments(segments)
        self.assertEqual(accepted[0]["avg_logprob"], -0.42)
        self.assertEqual(accepted[0]["no_speech_prob"], 0.05)
        self.assertEqual(accepted[0]["compression_ratio"], 1.2)

    def test_summarize_quality_picks_the_worst_side(self):
        segments = [
            self.FakeSegment("あ", avg_logprob=-0.2, no_speech_prob=0.1, compression_ratio=1.2),
            self.FakeSegment("い", avg_logprob=-0.9, no_speech_prob=0.5, compression_ratio=2.1),
        ]
        _text, accepted, _dropped = lt._collect_segments(segments)
        quality = lt.summarize_quality(accepted)
        self.assertEqual(quality["avg_logprob"], -0.9)
        self.assertEqual(quality["no_speech_prob"], 0.5)
        self.assertEqual(quality["compression_ratio"], 2.1)

    def test_summarize_quality_on_empty_input(self):
        self.assertEqual(lt.summarize_quality([]), {})


class ResultShapeTest(unittest.TestCase):
    """診断のために結果へ載せている情報が欠けていないこと。"""

    def test_skipped_result_marks_level_skip(self):
        result = lt._skipped_result("small", 0.0001, "below_absolute_silence<0.0002")
        self.assertTrue(result["level_skipped"])
        self.assertFalse(result["no_speech"])
        self.assertEqual(result["repetitive_dropped"], 0)

    def test_result_marks_no_speech_when_nothing_survived(self):
        result = lt._result("", [], "small", 0.01, [])
        self.assertTrue(result["no_speech"])
        self.assertFalse(result["level_skipped"])

    def test_result_counts_repetitive_drops(self):
        result = lt._result("", [], "small", 0.01, ["repetitive_segment(len=446)"])
        self.assertEqual(result["repetitive_dropped"], 1)


if __name__ == "__main__":
    unittest.main()
