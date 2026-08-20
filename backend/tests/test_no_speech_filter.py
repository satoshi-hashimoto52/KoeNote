"""segment 品質フィルタの回帰テスト。

無音判定は faster-whisper 本体と同じ複合条件
（no_speech_prob が閾値超 **かつ** avg_logprob が閾値未満）で行う。
no_speech_prob 単独で segment を破棄すると、realtime で窓が語の途中から始まる際に
確信度の高い音声まで丸ごと失われる（実測で 4 窓連続・約16秒が欠落した）。
"""
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.live_transcriber import (
    COMPRESSION_RATIO_THRESHOLD,
    HALLUCINATION_PHRASES,
    LOGPROB_THRESHOLD,
    NO_SPEECH_THRESHOLD,
    TRANSCRIBE_KWARGS,
    _collect_segments,
)


@dataclass
class FakeWord:
    start: float
    end: float
    word: str


@dataclass
class FakeSegment:
    """faster-whisper の segment が持つ判定用フィールドだけを再現する。"""
    text: str
    start: float = 0.0
    end: float = 1.0
    no_speech_prob: float = 0.0
    avg_logprob: float = -0.2
    compression_ratio: float = 1.0
    words: list = None

    def __post_init__(self):
        if self.words is None:
            self.words = [FakeWord(self.start, self.end, self.text)]


class NoSpeechFilterTest(unittest.TestCase):
    def test_high_no_speech_and_low_logprob_is_dropped_as_silence(self):
        """no_speech_prob が高く avg_logprob も低い -> 無音として除外。"""
        segment = FakeSegment(text="ノイズ", no_speech_prob=0.95, avg_logprob=-1.4)
        text, accepted, dropped = _collect_segments([segment])
        self.assertEqual(text, "")
        self.assertEqual(accepted, [])
        self.assertTrue(any("silence" in reason for reason in dropped), dropped)

    def test_high_no_speech_with_normal_logprob_is_kept(self):
        """no_speech_prob が高いが avg_logprob は正常 -> 除外しない。

        realtime で窓が語の途中から始まると実際に起きる（実測 0.62〜0.84 / logprob -0.16〜-0.35）。
        従来はここで segment 全体が捨てられ、発話が数十秒まとめて失われていた。
        """
        for no_speech, logprob in [(0.62, -0.27), (0.63, -0.23), (0.74, -0.16), (0.84, -0.35)]:
            with self.subTest(no_speech=no_speech, avg_logprob=logprob):
                segment = FakeSegment(text="消費電力を大幅に削減しつつ",
                                      no_speech_prob=no_speech, avg_logprob=logprob)
                text, accepted, dropped = _collect_segments([segment])
                self.assertEqual(text, "消費電力を大幅に削減しつつ")
                self.assertEqual(len(accepted), 1)
                self.assertEqual(dropped, [])

    def test_low_no_speech_is_kept_as_before(self):
        """no_speech_prob が低い -> 従来どおり保持。"""
        segment = FakeSegment(text="本日の会議を始めます", no_speech_prob=0.05, avg_logprob=-0.2)
        text, accepted, dropped = _collect_segments([segment])
        self.assertEqual(text, "本日の会議を始めます")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(dropped, [])

    def test_low_logprob_alone_is_still_dropped(self):
        """avg_logprob が閾値未満 -> 従来どおり除外（no_speech_prob が低くても）。"""
        segment = FakeSegment(text="あいまいな音", no_speech_prob=0.01,
                              avg_logprob=LOGPROB_THRESHOLD - 0.1)
        text, accepted, dropped = _collect_segments([segment])
        self.assertEqual(text, "")
        self.assertTrue(any("avg_logprob" in reason for reason in dropped), dropped)

    def test_compression_ratio_over_threshold_is_dropped(self):
        """compression_ratio 超過 -> 従来どおり除外（繰り返し・ハルシネーション対策）。"""
        segment = FakeSegment(text="あああああああああ", no_speech_prob=0.01, avg_logprob=-0.2,
                              compression_ratio=COMPRESSION_RATIO_THRESHOLD + 0.5)
        text, accepted, dropped = _collect_segments([segment])
        self.assertEqual(text, "")
        self.assertTrue(any("compression_ratio" in reason for reason in dropped), dropped)

    def test_hallucination_phrase_is_dropped(self):
        """ハルシネーション語句 -> 従来どおり除外。"""
        for phrase in sorted(HALLUCINATION_PHRASES):
            with self.subTest(phrase=phrase):
                segment = FakeSegment(text=phrase, no_speech_prob=0.01, avg_logprob=-0.2)
                text, accepted, dropped = _collect_segments([segment])
                self.assertEqual(text, "")
                self.assertIn("hallucination_phrase", dropped)

    def test_boundary_values_are_inclusive_as_documented(self):
        """閾値ちょうどの値では除外しない（> / < の境界を固定する）。"""
        segment = FakeSegment(text="境界", no_speech_prob=NO_SPEECH_THRESHOLD,
                              avg_logprob=LOGPROB_THRESHOLD,
                              compression_ratio=COMPRESSION_RATIO_THRESHOLD)
        text, accepted, dropped = _collect_segments([segment])
        self.assertEqual(text, "境界")
        self.assertEqual(dropped, [])

    def test_thresholds_match_the_values_passed_to_whisper(self):
        """フィルタの閾値と model.transcribe へ渡す値が一致していること。

        両者がずれると「whisper が返したのにこちらで捨てる」不整合が生まれる。
        """
        self.assertEqual(TRANSCRIBE_KWARGS["no_speech_threshold"], NO_SPEECH_THRESHOLD)
        self.assertEqual(TRANSCRIBE_KWARGS["log_prob_threshold"], LOGPROB_THRESHOLD)
        self.assertEqual(TRANSCRIBE_KWARGS["compression_ratio_threshold"],
                         COMPRESSION_RATIO_THRESHOLD)

    def test_mixed_segments_keep_only_the_valid_ones(self):
        segments = [
            FakeSegment(text="有効1", no_speech_prob=0.7, avg_logprob=-0.2),   # 保持
            FakeSegment(text="無音", no_speech_prob=0.9, avg_logprob=-1.5),     # 除外
            FakeSegment(text="有効2", no_speech_prob=0.1, avg_logprob=-0.3),   # 保持
            FakeSegment(text="繰り返し", no_speech_prob=0.1, avg_logprob=-0.3,
                        compression_ratio=3.0),                                # 除外
        ]
        text, accepted, dropped = _collect_segments(segments)
        self.assertEqual(text, "有効1有効2")
        self.assertEqual([a["text"] for a in accepted], ["有効1", "有効2"])
        self.assertEqual(len(dropped), 2)


if __name__ == "__main__":
    unittest.main()
