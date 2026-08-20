"""word 単位確定（方式G）の不変条件テスト。

不変条件:
  I1 すべての入力 word が最終結果に存在する
  I2 各 word の出現回数は厳密に1回
  I3 未処理の音声区間を飛び越えてカーソルを進めない
  I4 保持する状態量が録音時間に比例して増えない
  I5 drain 後に未確定 word が残らない
  I6 確定済みテキストは後続 window で変更されない

word ID（台本上の連番）は検証専用で、本番コードへは渡さない。
本番へ渡すのは start / end / text だけ。
"""
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from services.live_session import LiveSession, LiveSessionConfig
from services.pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE
from services.word_commit import (
    ANCHOR_WORDS,
    MAX_SAME_WINDOW_RETRIES,
    CURSOR_MARGIN_SECONDS,
    DRAIN_MAX_WINDOWS,
    JITTER_MAX_SECONDS,
    MAX_ERROR_RETRIES,
    DegradeReason,
    normalize_anchor_text,
)

FRAME_SAMPLES = 2048
PRESETS = [(8, 2, "低遅延"), (10, 2, "標準"), (12, 3, "精度優先"), (6, 2, "極端")]
SILENCE_GAP = 0.30


def loud_frame(samples: int = FRAME_SAMPLES) -> bytes:
    return np.full(samples, 8000, dtype="<i2").tobytes()


@dataclass(frozen=True)
class ScriptWord:
    wid: int          # 検証専用。本番コードへは渡さない
    start: float
    end: float
    text: str


class Script:
    """絶対時間軸上の発話台本と、それを窓ごとに文字化する whisper モデル。"""

    def __init__(self, words: list, tail_silence: float = 1.0):
        self.words = words
        self.total = words[-1].end + tail_silence if words else tail_silence

    @property
    def expected_text(self) -> str:
        return "".join(w.text for w in self.words)

    def transcribe(self, window_start: float, window_end: float, *, restyle: bool = False,
                   window_index: int = 0, drop_words: bool = False) -> list:
        """窓に完全に収まる word だけを、窓内相対時刻の segment として返す。

        窓外の音声は文字化できないという whisper の性質を再現する。
        """
        inside = [w for w in self.words
                  if w.start >= window_start - 1e-9 and w.end <= window_end + 1e-9]
        groups, current = [], []
        for w in inside:
            if current and w.start - current[-1].end > SILENCE_GAP:
                groups.append(current)
                current = []
            current.append(w)
        if current:
            groups.append(current)

        segments = []
        for group in groups:
            words = []
            for w in group:
                text = w.text
                if restyle and window_index % 2 == 0:
                    # 表記ゆれ: 窓ごとに句読点や空白の付き方が変わる
                    text = f" {text}、" if not text.endswith("。") else text.replace("。", "．")
                words.append({"start": w.start - window_start,
                              "end": w.end - window_start,
                              "text": text})
            segments.append({
                "start": group[0].start - window_start,
                "end": group[-1].end - window_start,
                "text": "".join(x["text"] for x in words),
                "words": [] if drop_words else words,
            })
        return segments


def build_words(spec: list, start: float = 0.5, dur: float = 0.45, gap: float = 0.05) -> list:
    """(text, ...) 列から連番 ID 付きの台本を作る。"""
    words, t, wid = [], start, 0
    for item in spec:
        if isinstance(item, tuple):
            text, d = item
        else:
            text, d = item, dur
        words.append(ScriptWord(wid, t, t + d, text))
        wid += 1
        t += d + gap
    return words


class Harness:
    """LiveSession を台本で駆動する。窓範囲は _infer_range をラップして取得する。"""

    def __init__(self, script: Script, chunk: float, overlap: float, **stub_kw):
        self.script = script
        self.stub_kw = stub_kw
        self.session = LiveSession(LiveSessionConfig(
            model="tiny", chunk_seconds=chunk, overlap_seconds=overlap,
            write_to_file=False, send_mode="pcm16"))
        self.window_calls = []
        self.history = []
        self.window_index = 0
        self.fail_windows = set()
        # 開始時刻がこの範囲に入る窓では whisper が何も返さない再現。
        # 実音声は存在するので rms は高いまま（= 無音ではない）。
        self.silent_ranges = []
        self._range = {}
        original = self.session._infer_range

        def infer(start_sample, end_sample):
            self._range["value"] = (start_sample, end_sample)
            return original(start_sample, end_sample)

        self.session._infer_range = infer

    def _stub(self, pcm, model, debug_save=False, sample_rate=SAMPLE_RATE):
        start_sample, end_sample = self._range["value"]
        self.window_index += 1
        if self.window_index in self.fail_windows:
            raise RuntimeError("推論失敗(再現)")
        ws, we = start_sample / SAMPLE_RATE, end_sample / SAMPLE_RATE
        if any(a - 1e-9 <= ws < b for a, b in self.silent_ranges):
            # 音声はあるのに whisper が何も返さない状況（rms は高いまま）
            return {"text": "", "segments": [], "model": "tiny", "rms": 0.2,
                    "skipped": True, "skip_reason": "", "debug_path": None,
                    "debug_wav_path": None}
        segments = self.script.transcribe(
            ws, we, window_index=self.window_index, **self.stub_kw)
        return {
            "text": "".join(s["text"] for s in segments),
            "segments": segments,
            "model": "tiny",
            # 語が無い窓は無音とみなす（実装は rms で「無音」と「文字化失敗」を区別する）
            "rms": 0.2 if segments else 0.001,
            "skipped": not segments,
            "skip_reason": "",
            "debug_path": None,
            "debug_wav_path": None,
        }

    def run(self, drain: bool = True):
        session = self.session
        with patch("services.live_session.transcribe_pcm16", new=self._stub):
            frames = int(self.script.total * SAMPLE_RATE / FRAME_SAMPLES) + 2
            for _ in range(frames):
                session.append_pcm(loud_frame())
                while True:
                    plan = session.plan_window()
                    if plan is None:
                        break
                    self.window_calls.append(plan)
                    result = session.run_window(*plan)
                    self.history.append(session.committed_text)
                    if result is None:
                        break                      # カーソルを進めない（再試行/破棄済み）
                    session.advance_cursor(plan[1])
            if drain:
                session.drain_on_stop()
                self.history.append(session.committed_text)
        session.finalize()
        return session


class InvariantMixin:
    def assert_invariants(self, harness, script, *, expect_exact=None):
        """word ID は検証専用。本番コードへは渡していない。

        一意テキスト台本では出現回数がそのまま ID の出現回数になる
        （部分文字列衝突を避けるため語番号はゼロ埋め固定幅にしている）。
        """
        session = harness.session
        text = session.committed_text.replace("\n", "")

        if expect_exact is not None:
            self.assertEqual(text, expect_exact, "確定テキストが期待と一致しない")
        else:
            # I1 / I2: 一意なテキストを使う台本では出現回数がそのまま検証になる
            for w in script.words:
                count = text.count(w.text)
                self.assertEqual(count, 1, f"wid={w.wid} {w.text!r} の出現回数が {count}")

        # I5: drain 後に未確定が残らない
        self.assertEqual(session.pending_words, [], "drain 後に未確定 word が残っている")

        # I6: 確定済みは後続 window で変更されない
        for before, after in zip(harness.history, harness.history[1:]):
            self.assertTrue(after.startswith(before), "確定済みテキストが後から変更された")

        # I3: 窓が後退せず、隣接窓に隙間がない
        for (_, prev_end), (next_start, _) in zip(harness.window_calls, harness.window_calls[1:]):
            self.assertLessEqual(next_start, prev_end + 1e-6, "窓に隙間がある（音声を飛び越えた）")
        # 推論失敗による同一窓の再試行は前進しないのが正しい。
        # 連続する「異なる」窓の間では必ず前進していること。
        distinct = [w for i, w in enumerate(harness.window_calls)
                    if i == 0 or w != harness.window_calls[i - 1]]
        for a, b in zip(distinct, distinct[1:]):
            self.assertGreater(b[1], a[1], "カーソルが前進していない")
        # 同一窓の連続処理は上限内に収まっていること
        run_length, longest = 1, 1
        for a, b in zip(harness.window_calls, harness.window_calls[1:]):
            run_length = run_length + 1 if a == b else 1
            longest = max(longest, run_length)
        self.assertLessEqual(longest, MAX_ERROR_RETRIES + MAX_SAME_WINDOW_RETRIES + 1,
                             f"同一窓を {longest} 回処理している")


class BoundaryCaseTest(InvariantMixin, unittest.TestCase):
    """テスト1〜3, 9: 確定境界をまたぐケース。"""

    def test_1_segment_straddles_committed_until(self):
        """segment.start < committed_until < segment.end に未確定 word が含まれる。"""
        # 「既」「確」「定」を確定させた後、次窓で同じ segment に「新」「規」が続く形
        words = build_words(["既", "確", "定", "新", "規", "終", "了"], dur=1.2, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                harness.run()
                self.assert_invariants(harness, script)

    def test_2_word_straddles_committed_until(self):
        """word.start < committed_until < word.end の境界語。"""
        # 確定線 (chunk-overlap) の上に word 境界が来るよう配置する
        words = build_words([("あ", 0.5), ("い", 0.5), ("う", 1.0), ("え", 0.5),
                             ("お", 0.5), ("か", 0.5)], start=5.6, dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                harness.run()
                self.assert_invariants(harness, script)

    def test_3_long_segment_straddles_stable_until(self):
        """stable_until をまたぐ長文 segment（無音で切れない）。"""
        words = build_words([f"文{i:04d}" for i in range(24)], dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                harness.run()
                self.assert_invariants(harness, script)

    def test_9_stop_in_the_middle_of_a_word(self):
        """セッション終了が word 途中・segment 途中になる。"""
        words = build_words(["これ", "で", "終わり", ("ます", 1.4)], dur=0.5, gap=0.0)
        script = Script(words, tail_silence=0.1)   # 最終 word の直後で停止
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                harness.run()
                self.assert_invariants(harness, script)


class ContinuousSpeechTest(InvariantMixin, unittest.TestCase):
    """テスト4, 8: 無音なし長文 x 全プリセット。"""

    def test_4_and_8_sixty_words_without_silence_all_presets(self):
        words = build_words([f"語{i:04d}" for i in range(60)], dur=0.45, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                session = harness.run()
                self.assert_invariants(harness, script)
                self.assertEqual(session.counters.unrecovered_seconds, 0.0)


class DrainMixTest(InvariantMixin, unittest.TestCase):
    """テスト5: drain で既確定分と未確定分が混在する。"""

    def test_5_drain_mixes_committed_and_pending(self):
        words = build_words([f"語{i:04d}" for i in range(14)], dur=0.6, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                session = harness.run(drain=False)
                # drain 前は未確定が残っている状態を作る
                before_text = session.committed_text
                with patch("services.live_session.transcribe_pcm16", new=harness._stub):
                    session.drain_on_stop()
                harness.history.append(session.committed_text)
                session.finalize()
                self.assertTrue(session.committed_text.startswith(before_text))
                self.assert_invariants(harness, script)


class RepeatedWordTest(InvariantMixin, unittest.TestCase):
    """テスト6 + 追加: 正当な繰り返し語を消さない。"""

    def test_6_consecutive_identical_words(self):
        spec = ["はい", "はい", "はい", "次に", "次に", "そうです", "そうです", "そうです"]
        script = Script(build_words(spec, dur=0.5, gap=0.05))
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                session = harness.run()
                self.assert_invariants(harness, script, expect_exact=script.expected_text)
                self.assertEqual(session.committed_text.replace("\n", "").count("はい"), 3)

    def test_anchor_words_all_identical(self):
        """アンカー6語がすべて同じ単語。"""
        script = Script(build_words(["ええ"] * ANCHOR_WORDS + ["以上", "です"], dur=0.5, gap=0.0))
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                harness.run()
                self.assert_invariants(harness, script, expect_exact=script.expected_text)

    def test_repetition_longer_than_anchor(self):
        """アンカー語数より長い繰り返し。"""
        count = ANCHOR_WORDS * 3
        script = Script(build_words(["あの"] * count + ["終わり"], dur=0.4, gap=0.0))
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                session = harness.run()
                self.assert_invariants(harness, script, expect_exact=script.expected_text)
                self.assertEqual(session.committed_text.replace("\n", "").count("あの"), count)


class RestyleTest(InvariantMixin, unittest.TestCase):
    """テスト7 + 追加: 窓ごとに句読点・空白・全角半角が変わる。"""

    def test_7_punctuation_and_spacing_varies_between_windows(self):
        words = build_words([f"語{i:04d}" for i in range(30)], dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap, restyle=True)
                harness.run()
                # 表記ゆれで句読点・空白が付くため、正規化して照合する
                text = normalize_anchor_text(harness.session.committed_text)
                for w in words:
                    self.assertEqual(text.count(normalize_anchor_text(w.text)), 1,
                                     f"wid={w.wid} {w.text!r} の出現回数が 1 でない")
                self.assertEqual(harness.session.pending_words, [])

    def test_anchor_normalization_covers_width_and_punctuation(self):
        cases = [
            ("はい。", "はい"), ("はい、", "はい"), (" はい ", "はい"), ("はい", "はい"),
            ("次に、", "次に"), ("次に．", "次に"), ("次に.", "次に"), ("次に,", "次に"),
            ("全角　空白", "全角空白"), ("半角 空白", "半角空白"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_anchor_text(raw), expected)
        # 語の一部になる文字は落とさない
        for keep in ["コーヒー", "々", "ヴ", "ー"]:
            self.assertEqual(normalize_anchor_text(keep), keep)


class DegradationTest(InvariantMixin, unittest.TestCase):
    """縮退: words なし・例外・リングバッファ外。"""

    def test_words_missing_for_consecutive_windows(self):
        """words なしが複数 window 連続する。"""
        words = build_words([f"語{i:04d}" for i in range(20)], dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap, drop_words=True)
                session = harness.run()
                self.assertGreater(session.counters.pseudo_word_count, 0)
                self.assertGreater(session.counters.degraded_window_count, 0)
                self.assertIn(DegradeReason.SEGMENT_WITHOUT_WORDS,
                              "\n".join(session.degrade_log))
                self.assertEqual(session.pending_words, [])
                # 擬似 word でも全文が失われない
                for w in words:
                    self.assertGreaterEqual(session.committed_text.count(w.text), 1)

    def test_inference_error_beyond_retry_limit(self):
        """Whisper 例外がリトライ上限を超える。"""
        words = build_words([f"語{i:04d}" for i in range(20)], dur=0.5, gap=0.0)
        script = Script(words)
        harness = Harness(script, 10, 2)
        # 最初の窓で MAX_ERROR_RETRIES を超える回数失敗させる
        harness.fail_windows = set(range(1, MAX_ERROR_RETRIES + 3))
        session = harness.run()
        self.assertGreaterEqual(session.counters.inference_error_count, MAX_ERROR_RETRIES + 1)
        log = "\n".join(session.degrade_log)
        self.assertIn(DegradeReason.INFERENCE_FAILED, log)
        self.assertIn(DegradeReason.INFERENCE_RETRY_EXHAUSTED, log)
        # 取り逃した区間は drain で回収される
        self.assertEqual(session.pending_words, [])
        self.assert_invariants(harness, script)

    def test_pending_head_outside_ring_buffer(self):
        """pending 先頭がリングバッファ範囲外になる。"""
        words = build_words([f"語{i:04d}" for i in range(40)], dur=0.5, gap=0.0)
        script = Script(words)
        harness = Harness(script, 10, 2)
        # 容量を極小にして、確定より前の区間が破棄される状況を作る
        harness.session.pcm.capacity_samples = 12 * SAMPLE_RATE
        harness.session.pcm.trim_slack_samples = SAMPLE_RATE
        session = harness.run()
        # 破棄が起きても pending は残さず、窓は常にバッファ内に収まる
        self.assertEqual(session.pending_words, [])
        for start, _end in harness.window_calls:
            self.assertGreaterEqual(start, 0)

    def test_forced_commit_is_logged_and_counted(self):
        """forced commit の発生がカウンターとログに残る。"""
        # 確定線をまたぎ、かつ窓先頭から始まる長い word を作る
        words = build_words([("長い語", 9.0), ("次", 0.5)], start=0.0, gap=0.0)
        script = Script(words)
        harness = Harness(script, 10, 2)
        session = harness.run()
        self.assertGreater(session.counters.forced_commit_count, 0)
        self.assertIn(DegradeReason.FORCED_COMMIT, "\n".join(session.degrade_log))
        self.assertEqual(session.pending_words, [])


class WhisperSkippedWindowTest(InvariantMixin, unittest.TestCase):
    """whisper が窓の中身を文字化しなかった場合に音声を飛び越えないこと。

    実測: chunk=12/overlap=3 で 12 秒窓のうち先頭 9.2 秒に segment が 1 つも返らず、
    その区間（8.2 秒 = 1 文）が確定されないままカーソルが通過して失われた。
    未文字化区間は pending にも入らないため、保留だけを見るカーソル追従では守れない。
    確定端を基準に引き戻して別の窓境界で再試行する必要がある。
    """

    def test_window_yielding_nothing_does_not_skip_audio(self):
        words = build_words([f"語{i:04d}" for i in range(30)], dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                harness = Harness(script, chunk, overlap)
                # 2 番目の窓（step だけ進んだ位置）を無出力にする
                step = chunk - overlap
                harness.silent_ranges = [(float(step) - 0.01, float(step) + 0.01)]
                session = harness.run()
                self.assert_invariants(harness, script)
                self.assertEqual(session.counters.untranscribed_seconds, 0.0)

    def test_silent_audio_does_not_stall_the_cursor(self):
        """無音では確定が進まないのが正常。最小前進で這わないこと。"""
        # 前半に発話、後半は無音のみ
        words = build_words([f"語{i:04d}" for i in range(8)], dur=0.5, gap=0.0)
        script = Script(words, tail_silence=20.0)
        harness = Harness(script, 10, 2)
        session = harness.run()
        # 無音区間を最小前進(0.5s)で這うと窓数が膨らむ。step 相当で進んでいること。
        self.assertLess(len(harness.window_calls), 12,
                        f"無音で窓が増えすぎている ({len(harness.window_calls)} 窓)")
        self.assertEqual(session.counters.cursor_min_advance_count, 0)
        self.assert_invariants(harness, script)

    def test_persistently_untranscribable_window_gives_up_and_counts_it(self):
        """再試行上限まで文字化できなければ、諦めて前進し秒数を計上する。"""
        words = build_words([f"語{i:04d}" for i in range(80)], dur=0.5, gap=0.0)
        script = Script(words)
        harness = Harness(script, 10, 2)
        # 窓開始がこの範囲にある限り whisper が何も返さない（音声は存在する）
        harness.silent_ranges = [(8.0, 30.0)]
        session = harness.run()
        self.assertGreater(session.counters.untranscribed_seconds, 0.0,
                           "諦めた秒数が計上されていない")
        self.assertIn(DegradeReason.AUDIO_LEFT_UNTRANSCRIBED, "\n".join(session.degrade_log))
        self.assertIn(DegradeReason.WINDOW_YIELDED_NOTHING, "\n".join(session.degrade_log))
        # 諦めても停止せず、以降の音声は確定される
        self.assertEqual(session.pending_words, [])
        self.assertGreater(len(session.committed_text), 0)


class StabilityTest(InvariantMixin, unittest.TestCase):
    """状態量の有界性と再現性。"""

    def test_state_stays_constant_over_one_hour(self):
        """1時間相当の長文で状態量が一定であること。"""
        words = build_words([f"語{i:04d}" for i in range(8000)], dur=0.44, gap=0.0)
        script = Script(words)
        harness = Harness(script, 10, 2)
        session = harness.session
        max_pending = 0
        max_anchor = 0
        with patch("services.live_session.transcribe_pcm16", new=harness._stub):
            frames = int(script.total * SAMPLE_RATE / FRAME_SAMPLES) + 2
            for _ in range(frames):
                session.append_pcm(loud_frame())
                while True:
                    plan = session.plan_window()
                    if plan is None:
                        break
                    if session.run_window(*plan) is None:
                        break
                    session.advance_cursor(plan[1])
                    max_pending = max(max_pending, len(session.pending_words))
                    max_anchor = max(max_anchor, len(session.committed_words))
            session.drain_on_stop()

        self.assertGreater(session.recorded_seconds, 3500, "1時間相当を処理していない")
        self.assertLessEqual(max_anchor, ANCHOR_WORDS, "アンカーが上限を超えて増えている")
        self.assertLess(max_pending, 200, f"pending が増え続けている ({max_pending})")
        self.assertLessEqual(len(session.degrade_log), 200)
        self.assertLessEqual(session.pcm.buffered_bytes,
                             session.pcm.capacity_bytes
                             + session.pcm.trim_slack_samples * BYTES_PER_SAMPLE)

    def test_repeated_runs_are_deterministic(self):
        """同じ入力を複数回実行して結果が安定すること。"""
        words = build_words([f"語{i:04d}" for i in range(30)], dur=0.5, gap=0.0)
        script = Script(words)
        for chunk, overlap, label in PRESETS:
            with self.subTest(preset=label):
                outputs = []
                for _ in range(3):
                    harness = Harness(script, chunk, overlap)
                    outputs.append(harness.run().committed_text)
                self.assertEqual(len(set(outputs)), 1, "実行ごとに結果が変わる")


class WordTimestampsEnabledTest(unittest.TestCase):
    """word 単位の確定には word_timestamps が必須。無効化されたら気付けるようにする。"""

    def test_word_timestamps_is_enabled(self):
        from services.live_transcriber import TRANSCRIBE_KWARGS
        self.assertTrue(TRANSCRIBE_KWARGS["word_timestamps"],
                        "word_timestamps を無効にすると確定線をまたぐ語が失われる")


class CursorTest(unittest.TestCase):
    """カーソル更新式と安全性。"""

    def _session(self, chunk=10, overlap=2):
        return LiveSession(LiveSessionConfig(
            model="tiny", chunk_seconds=chunk, overlap_seconds=overlap,
            write_to_file=False, send_mode="pcm16"))

    def test_cursor_advances_by_step_without_pending(self):
        session = self._session()
        session.advance_cursor(10 * SAMPLE_RATE)
        self.assertEqual(session.next_window_end, 18 * SAMPLE_RATE)

    def test_cursor_follows_pending_head_with_margin(self):
        from services.word_commit import CommitWord
        session = self._session()
        session.pending_words = [CommitWord(start=12.0, end=17.0, text="x")]
        session.advance_cursor(18 * SAMPLE_RATE)
        expected = int(round((12.0 - CURSOR_MARGIN_SECONDS) * SAMPLE_RATE)) + 10 * SAMPLE_RATE
        self.assertEqual(session.next_window_end, expected)
        # 次窓の開始が未確定音声の先頭より手前にあること
        self.assertLessEqual((session.next_window_end - session.chunk_samples) / SAMPLE_RATE, 12.0)

    def test_cursor_never_stalls(self):
        from services.word_commit import CommitWord
        session = self._session()
        # pending 先頭が窓先頭と同じ = 追従先が前進しないケース
        session.pending_words = [CommitWord(start=0.0, end=9.0, text="x")]
        session.advance_cursor(10 * SAMPLE_RATE)
        self.assertGreater(session.next_window_end, 10 * SAMPLE_RATE)
        self.assertGreater(session.counters.cursor_min_advance_count, 0)

    def test_window_outside_ring_buffer_is_rejected(self):
        session = self._session()
        session.pcm.capacity_samples = 5 * SAMPLE_RATE
        session.pcm.trim_slack_samples = SAMPLE_RATE
        for _ in range(int(30 * SAMPLE_RATE / FRAME_SAMPLES)):
            session.append_pcm(loud_frame())
        session.next_window_end = session.chunk_samples   # 破棄済みの先頭を指す
        plan = session.plan_window()
        if plan is not None:
            self.assertGreaterEqual(plan[0], session.pcm.earliest_sample)


if __name__ == "__main__":
    unittest.main()
