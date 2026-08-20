import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from services.pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE, PcmRingBuffer, Resampler16k


def pcm(value: int, samples: int) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


class PcmRingBufferTest(unittest.TestCase):
    def test_absolute_indexing_round_trip(self):
        ring = PcmRingBuffer(capacity_seconds=10.0)
        ring.append(pcm(11, SAMPLE_RATE))
        ring.append(pcm(22, SAMPLE_RATE))
        self.assertEqual(ring.total_samples, 2 * SAMPLE_RATE)

        first = ring.read(0, SAMPLE_RATE)
        second = ring.read(SAMPLE_RATE, 2 * SAMPLE_RATE)
        self.assertEqual(np.frombuffer(first, dtype="<i2").tolist(), [11] * SAMPLE_RATE)
        self.assertEqual(np.frombuffer(second, dtype="<i2").tolist(), [22] * SAMPLE_RATE)

    def test_evicted_range_returns_none_instead_of_wrong_audio(self):
        # クランプして別区間を返すと「違う音声を違う絶対時刻に紐付ける」ため、None でなければならない。
        ring = PcmRingBuffer(capacity_seconds=2.0, trim_slack_seconds=0.5)
        for _ in range(8):
            ring.append(pcm(1, SAMPLE_RATE))
        self.assertGreater(ring.earliest_sample, 0)
        self.assertIsNone(ring.read(0, SAMPLE_RATE))
        self.assertIsNone(ring.read(ring.earliest_sample - 1, ring.earliest_sample + 10))
        self.assertIsNone(ring.read(ring.total_samples - 5, ring.total_samples + 5))
        self.assertIsNone(ring.read(100, 100))
        self.assertIsNotNone(ring.read(ring.earliest_sample, ring.earliest_sample + 10))

    def test_total_samples_stays_exact_after_eviction(self):
        ring = PcmRingBuffer(capacity_seconds=2.0, trim_slack_seconds=0.5)
        for _ in range(60):
            ring.append(pcm(3, SAMPLE_RATE // 2))
        self.assertEqual(ring.total_samples, 30 * SAMPLE_RATE)
        self.assertEqual(ring.total_samples, ring.earliest_sample + ring.buffered_bytes // BYTES_PER_SAMPLE)

    def test_memory_is_bounded_by_capacity_plus_slack(self):
        ring = PcmRingBuffer(capacity_seconds=5.0, trim_slack_seconds=1.0)
        limit = ring.capacity_bytes + ring.trim_slack_samples * BYTES_PER_SAMPLE
        for _ in range(600):  # 10 分ぶん
            ring.append(pcm(7, SAMPLE_RATE))
            self.assertLessEqual(ring.buffered_bytes, limit)

    def test_odd_byte_input_is_truncated_not_misaligned(self):
        ring = PcmRingBuffer()
        ring.append(b"\x01\x02\x03")
        self.assertEqual(ring.total_samples, 1)
        self.assertEqual(ring.buffered_bytes, 2)

    def test_append_silence_advances_timeline(self):
        ring = PcmRingBuffer()
        ring.append(pcm(5, 100))
        ring.append_silence(SAMPLE_RATE)
        self.assertEqual(ring.total_samples, 100 + SAMPLE_RATE)
        gap = ring.read(100, 100 + SAMPLE_RATE)
        self.assertEqual(np.frombuffer(gap, dtype="<i2").max(), 0)


class Resampler16kTest(unittest.TestCase):
    def test_passthrough_at_target_rate(self):
        rs = Resampler16k(SAMPLE_RATE)
        self.assertTrue(rs.passthrough)
        data = pcm(9, 64)
        self.assertEqual(rs.process(data), data)

    def test_48k_to_16k_preserves_duration_across_chunk_boundaries(self):
        rs = Resampler16k(48000)
        seconds = 3
        t = np.arange(48000 * seconds) / 48000.0
        source = (np.sin(2 * np.pi * 440 * t) * 10000).astype("<i2").tobytes()
        out = b"".join(rs.process(source[i:i + 4096]) for i in range(0, len(source), 4096))
        produced = len(out) // BYTES_PER_SAMPLE
        expected = SAMPLE_RATE * seconds
        # 位相を持ち越すので、3 秒でも誤差は 1 チャンク未満に収まる。
        self.assertLess(abs(produced - expected), SAMPLE_RATE * 0.01)

    def test_no_phase_discontinuity_at_seams(self):
        rs = Resampler16k(48000)
        t = np.arange(48000) / 48000.0
        amplitude = 10000
        source = (np.sin(2 * np.pi * 440 * t) * amplitude).astype("<i2").tobytes()
        out = b"".join(rs.process(source[i:i + 4096]) for i in range(0, len(source), 4096))
        arr = np.frombuffer(out, dtype="<i2").astype(np.float64)
        # 440Hz/16kHz の 1 サンプルあたり理論最大差分。継ぎ目に段差があればこれを超える。
        theoretical = amplitude * 2 * np.pi * 440 / SAMPLE_RATE
        self.assertLess(np.abs(np.diff(arr)).max(), theoretical * 1.5)


if __name__ == "__main__":
    unittest.main()
