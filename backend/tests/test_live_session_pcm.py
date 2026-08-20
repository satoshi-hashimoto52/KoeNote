import itertools
import sys
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from services.live_session import BUFFER_CAPACITY_SECONDS, LiveSession, LiveSessionConfig
from services.pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE

FRAME_SAMPLES = 2048  # renderer の worklet が送る 1 フレーム（128ms）


def loud_frame(samples: int = FRAME_SAMPLES) -> bytes:
    return np.full(samples, 8000, dtype="<i2").tobytes()


def stub_result(text="あ", start=0.0, end=8.0, **over):
    payload = {
        "text": text,
        "segments": [{"start": start, "end": end, "text": text}] if text else [],
        "model": "tiny",
        "rms": 0.2,
        "skipped": not bool(text),
        "skip_reason": "",
        "debug_path": None,
        "debug_wav_path": None,
    }
    payload.update(over)
    return payload


def make_session(**over) -> LiveSession:
    kwargs = dict(
        model="tiny",
        chunk_seconds=10,
        overlap_seconds=2,
        write_to_file=False,
        send_mode="pcm16",
    )
    kwargs.update(over)
    return LiveSession(LiveSessionConfig(**kwargs))


def drain(session, transcribe_patch=None):
    """処理可能な窓をすべて消化する。"""
    results = []
    while True:
        plan = session.plan_window()
        if plan is None:
            return results
        result = session.run_window(*plan)
        session.advance_cursor(plan[1])
        if result is not None:
            results.append(result)


class WindowCadenceTest(unittest.TestCase):
    def test_windows_advance_by_exact_integer_step(self):
        session = make_session()
        chunk, step = session.chunk_samples, session.step_samples
        self.assertEqual(chunk, 10 * SAMPLE_RATE)
        self.assertEqual(step, 8 * SAMPLE_RATE)

        planned = []
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result()):
            for _ in range(60 * 8):  # 60 秒
                session.append_pcm(loud_frame())
                while (plan := session.plan_window()) is not None:
                    planned.append(plan)
                    session.run_window(*plan)
                    session.advance_cursor(plan[1])

        self.assertEqual(
            planned[:4],
            [
                (0, 10 * SAMPLE_RATE),
                (8 * SAMPLE_RATE, 18 * SAMPLE_RATE),
                (16 * SAMPLE_RATE, 26 * SAMPLE_RATE),
                (24 * SAMPLE_RATE, 34 * SAMPLE_RATE),
            ],
        )
        # 整数サンプルのカーソンなので、何窓進んでも float 累積のドリフトが出ない。
        for index, (start, end) in enumerate(planned):
            self.assertEqual(end - start, chunk)
            self.assertEqual(end, chunk + index * step)

    def test_no_window_is_planned_before_first_full_chunk(self):
        session = make_session()
        for _ in range(9 * 8):  # 9 秒 < chunk 10 秒
            session.append_pcm(loud_frame())
        self.assertIsNone(session.plan_window())


class TimestampParityTest(unittest.TestCase):
    """レガシー webm 経路と同じコミット/暫定判定になることの回帰ガード。"""

    def test_pcm_path_matches_legacy_commit_split(self):
        session = make_session(chunk_seconds=5, overlap_seconds=1)
        results = [
            stub_result(),  # 差し替えは side_effect で行う
        ]
        window_results = [
            {**stub_result(), "text": "A B C",
             "segments": [{"start": 0, "end": 3, "text": "A"}, {"start": 3, "end": 5, "text": "B C"}]},
            {**stub_result(), "text": "B C D",
             "segments": [{"start": 0, "end": 2, "text": "B C"}, {"start": 2, "end": 5, "text": "D"}]},
        ]
        with patch("services.live_session.transcribe_pcm16", side_effect=window_results) as transcribe:
            # 窓1: [0,5) -> stable_until 4 -> "A"(0-3) を確定、"B C"(3-5) は暫定
            for _ in range(5 * SAMPLE_RATE // FRAME_SAMPLES + 1):
                session.append_pcm(loud_frame())
            first = session.run_window(*session.plan_window())
            session.advance_cursor(int(5 * SAMPLE_RATE))

            # 窓2: [4,9) -> stable_until 8 -> "B C"(4-6) を確定、"D"(6-9) は暫定
            while session.pcm.total_samples < 9 * SAMPLE_RATE:
                session.append_pcm(loud_frame())
            second = session.run_window(4 * SAMPLE_RATE, 9 * SAMPLE_RATE)

        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(first["final"], "A")
        self.assertEqual(second["final"], "B C")
        self.assertEqual(session.committed_text, "AB C")
        self.assertEqual(session.partial_text, "D")
        # finalize は無条件追記しない。末尾は drain_on_stop が同じ判定で確定させる。
        with patch("services.live_session.transcribe_pcm16",
                   return_value={**stub_result(), "text": "D",
                                 "segments": [{"start": 4.0, "end": 5.0, "text": "D"}]}):
            session.drain_on_stop()
        self.assertIn("D", session.finalize()["committed_text"])
        self.assertEqual(session.pending_words, [])
        self.assertIsNotNone(session.last_audio_received_at)
        self.assertIsNotNone(session.last_transcription_at)

    def test_segment_without_words_becomes_a_pseudo_word(self):
        """word 情報が無い segment は擬似 word になり、確定/保留の通常経路に載る。

        segment 全体を無条件に確定も破棄もしない。
        """
        session = make_session(chunk_seconds=5, overlap_seconds=1)
        with patch("services.live_session.transcribe_pcm16",
                   return_value={**stub_result(),
                                 "text": "認識結果",
                                 "segments": [{"start": 0.0, "end": 3.0, "text": "認識結果"}]}):
            while session.pcm.total_samples < 5 * SAMPLE_RATE:
                session.append_pcm(loud_frame())
            payload = session.run_window(*session.plan_window())

        self.assertEqual(session.counters.pseudo_word_count, 1)
        self.assertGreaterEqual(session.counters.degraded_window_count, 1)
        # stable_until=4 なので [0,3] の擬似 word は確定される
        self.assertEqual(payload["committed_append"], "認識結果")


class CommittedDeltaTest(unittest.TestCase):
    def test_delta_reconstructs_the_full_transcript(self):
        session = make_session()
        words = ["これは", "テストです。", "次の文", "さらに続く。", "終わり"]
        counter = itertools.count()

        def stub(pcm, model, debug_save=False, sample_rate=SAMPLE_RATE):
            word = words[next(counter) % len(words)]
            return {**stub_result(), "text": word,
                    "segments": [{"start": 0.0, "end": 8.0, "text": word}]}

        deltas = []
        with patch("services.live_session.transcribe_pcm16", new=stub):
            for _ in range(400 * 8):
                session.append_pcm(loud_frame())
                while (plan := session.plan_window()) is not None:
                    before = session.committed_text
                    result = session.run_window(*plan)
                    session.advance_cursor(plan[1])
                    if result is None:
                        continue
                    self.assertEqual(result["committed_length_before"], len(before))
                    self.assertFalse(result["needs_snapshot"])
                    # クライアントは prev + delta で全文を再構成できなければならない。
                    self.assertEqual(before + result["committed_delta"], session.committed_text)
                    deltas.append(result["committed_delta"])

        self.assertEqual("".join(deltas), session.committed_text)
        self.assertGreater(len(session.committed_text), 0)


class CatchUpTest(unittest.TestCase):
    def test_lagging_inference_consumes_backlog_in_order_without_dropping(self):
        session = make_session()
        # 90 秒ぶんを一切排出せずに投入（容量 180 秒以内なので落ちてはいけない）。
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result()):
            for _ in range(90 * 8):
                session.append_pcm(loud_frame())
            self.assertEqual(session.dropped_samples, 0)
            results = drain(session)

        self.assertEqual(session.dropped_samples, 0)
        self.assertFalse(session.degraded)
        self.assertGreater(len(results), 8)

    def test_overflowing_backlog_drops_explicitly_and_never_reads_evicted_audio(self):
        session = make_session()
        capacity = int(BUFFER_CAPACITY_SECONDS)
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result()):
            # 容量を大きく超える 400 秒を排出せずに投入する。
            for _ in range((capacity + 220) * 8):
                session.append_pcm(loud_frame())
            plans = []
            while (plan := session.plan_window()) is not None:
                plans.append(plan)
                # 破棄済み区間を計画してはいけない = run_window が None を返さない。
                self.assertIsNotNone(session.run_window(*plan), f"evicted window planned: {plan}")
                session.advance_cursor(plan[1])

        self.assertGreater(session.dropped_samples, 0)
        self.assertTrue(session.degraded)
        for start, _end in plans:
            self.assertGreaterEqual(start, 0)


class FlushTailTest(unittest.TestCase):
    def test_flush_tail_recovers_audio_beyond_the_cursor(self):
        session = make_session()
        with patch("services.live_session.transcribe_pcm16", return_value=stub_result()):
            for _ in range(14 * 8):  # 14 秒: 窓1 (0-10) は処理され、残り 4 秒が端切れになる
                session.append_pcm(loud_frame())
            drain(session)
            self.assertIsNone(session.plan_window())

            with patch("services.live_session.transcribe_pcm16",
                       return_value={**stub_result(), "text": "末尾",
                                     "segments": [{"start": 9.0, "end": 10.0, "text": "末尾"}]}):
                tail = session.flush_tail()

        self.assertIsNotNone(tail)
        self.assertIn("末尾", session.committed_text)

    def test_flush_tail_returns_none_when_nothing_remains(self):
        session = make_session()
        self.assertIsNone(session.flush_tail())


class LongRunStabilityTest(unittest.TestCase):
    """要件「1〜2時間の連続動作で処理量とメモリが増え続けない」を機械的に検証する。

    Whisper をスタブ化し、2 時間ぶんの音声を実時間を待たずに投入する。
    """

    def test_two_hours_of_audio_is_constant_cost_and_bounded_memory(self):
        session = make_session()
        chunk_bytes = session.chunk_samples * BYTES_PER_SAMPLE
        limit = session.pcm.capacity_bytes + session.pcm.trim_slack_samples * BYTES_PER_SAMPLE
        # 16000/2048 は割り切れないので、秒 x フレーム毎秒 で数えると 10% ずれる。
        # 総サンプル数から逆算して正確に 2 時間ぶんにする。
        frames = (2 * 60 * 60 * SAMPLE_RATE) // FRAME_SAMPLES
        sample_every = (5 * 60 * SAMPLE_RATE) // FRAME_SAMPLES

        # unittest.mock は call_args_list に渡された PCM を全部保持するため、
        # Mock で受けると「本番のメモリ」ではなく「モックの保持量」を測ってしまう
        # （2時間で約300MB）。素の関数で長さだけ記録する。
        window_byte_sizes = []

        def stub(pcm, model, debug_save=False, sample_rate=SAMPLE_RATE):
            window_byte_sizes.append(len(pcm))
            return stub_result()

        buffered_samples = []
        traced_samples = []
        tracemalloc.start()
        try:
            with patch("services.live_session.transcribe_pcm16", new=stub):
                for i in range(frames):
                    session.append_pcm(loud_frame())
                    while (plan := session.plan_window()) is not None:
                        session.run_window(*plan)
                        session.advance_cursor(plan[1])
                    if i % sample_every == 0:
                        buffered_samples.append(session.pcm.buffered_bytes)
                        traced_samples.append(tracemalloc.get_traced_memory()[0])
        finally:
            tracemalloc.stop()

        # 1) リングバッファは容量で頭打ちになる（経過時間に比例しない）。
        self.assertLessEqual(max(buffered_samples), limit)

        # 2) 1 窓の処理コストが一定であること。「全部 join する」実装が
        #    再導入されたら即座に落ちる、このスイートで最も重要な不変条件。
        self.assertTrue(window_byte_sizes)
        for size in window_byte_sizes:
            self.assertEqual(size, chunk_bytes)

        # 3) 窓を 1 つも取りこぼさず、音声も落としていない。
        expected_windows = (session.pcm.total_samples - session.chunk_samples) // session.step_samples + 1
        self.assertEqual(len(window_byte_sizes), expected_windows)
        self.assertEqual(session.dropped_samples, 0)
        self.assertFalse(session.degraded)

        # 4) Python 割当が後半で増え続けない（committed_text の線形増加ぶんだけ許容）。
        half = len(traced_samples) // 2
        growth = max(traced_samples[half:]) - max(traced_samples[:half])
        self.assertLess(growth, 4_000_000, f"traced memory grew {growth} bytes in the second half")
        self.assertLess(max(traced_samples), 40_000_000, "peak traced memory should stay small")

        # 5) 2 時間ぶん処理できている。
        self.assertAlmostEqual(session.recorded_seconds, 7200, delta=1.0)
        self.assertGreater(session.processed_audio_seconds, 7100)


if __name__ == "__main__":
    unittest.main()


class SegmentBoundaryRegressionTest(unittest.TestCase):
    """確定線をまたぐ segment のテキストが失われないことの回帰テスト。

    stable_until は次 window の開始時刻と数学的に一致するため、segment 単位の
    「確定線までに終わったものだけ確定する」判定では、またぐ segment が
    再評価されないまま消えていた（docs/issues/0001）。
    word 単位の確定に変えることで解消される。

    レガシー webm 経路は当時の挙動を保った回帰用フィクスチャなので、
    そちらは従来どおり欠落することを併せて確認する。
    """

    WINDOWS = [
        {**stub_result(), "text": "前半", "segments": [{"start": 0.0, "end": 6.0, "text": "前半"}]},
        # window 全体を覆う 1 segment。stable_until(16) をまたぐため暫定に回る。
        {**stub_result(), "text": "中間", "segments": [{"start": 0.0, "end": 10.0, "text": "中間"}]},
        {**stub_result(), "text": "後半", "segments": [{"start": 1.0, "end": 8.0, "text": "後半"}]},
    ]

    def _run_pcm_windows(self) -> str:
        session = make_session()
        with patch("services.live_session.transcribe_pcm16", side_effect=self.WINDOWS):
            for index in range(3):
                target = (10 + index * 8) * SAMPLE_RATE
                while session.pcm.total_samples < target:
                    session.append_pcm(loud_frame())
                plan = session.plan_window()
                self.assertIsNotNone(plan)
                session.run_window(*plan)
                session.advance_cursor(plan[1])
        return session.finalize()["committed_text"]

    def test_straddling_segment_is_not_lost(self):
        """word 単位確定により、確定線をまたぐ segment も失われない。"""
        self.assertIn("中間", self._run_pcm_windows())

    def test_legacy_webm_path_loses_it_identically(self):
        """PCM 化による回帰ではないことの証拠。"""
        session = LiveSession(
            LiveSessionConfig(
                model="tiny", chunk_seconds=10, overlap_seconds=2, write_to_file=False, send_mode="full"
            )
        )
        with patch(
            "services.live_session.convert_webm_bytes_to_wav",
            return_value={"wav_bytes": b"w", "debug_path": None, "debug_wav_path": None},
        ), patch.object(session, "_wav_duration_seconds", side_effect=[10.0, 18.0, 26.0]), patch.object(
            session, "_extract_tail_wav"
        ), patch("services.live_session.transcribe_wav_file", side_effect=self.WINDOWS):
            for _ in range(3):
                session.transcribe_chunk(b"x")
        self.assertEqual(session.finalize()["committed_text"], "前半後半")
