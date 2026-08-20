import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from services.pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE
from services.wav_recorder import (
    RIFF_HEADER_SIZE,
    AsyncWavAppender,
    CrashSafeWavWriter,
    repair_wav_header,
)


def tone(samples: int) -> bytes:
    return (np.sin(np.arange(samples) * 0.1) * 12000).astype("<i2").tobytes()


class CrashSafeWavWriterTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bridgelog_wavtest_"))
        self.path = self.dir / "recording.wav"

    def test_closed_file_is_readable_with_expected_frames(self):
        writer = CrashSafeWavWriter(self.path)
        writer.append(tone(SAMPLE_RATE))
        writer.append(tone(SAMPLE_RATE // 2))
        writer.close()

        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), BYTES_PER_SAMPLE)
            self.assertEqual(wav.getframerate(), SAMPLE_RATE)
            self.assertEqual(wav.getnframes(), SAMPLE_RATE + SAMPLE_RATE // 2)

    def test_killed_process_still_leaves_a_playable_file(self):
        # sync 間隔ぶんは書き戻されているので、close() されなくても再生できる。
        writer = CrashSafeWavWriter(self.path, sync_interval_bytes=SAMPLE_RATE * BYTES_PER_SAMPLE)
        for _ in range(5):
            writer.append(tone(SAMPLE_RATE))
        writer._fh.close()  # SIGKILL 相当: close() を通らない
        writer._fh = None

        with wave.open(str(self.path), "rb") as wav:
            self.assertGreaterEqual(wav.getnframes(), 4 * SAMPLE_RATE)

    def test_repair_recovers_length_when_header_is_stale(self):
        writer = CrashSafeWavWriter(self.path, sync_interval_bytes=10**9)  # 書き戻しを起こさせない
        for _ in range(3):
            writer.append(tone(SAMPLE_RATE))
        writer._fh.flush()
        writer._fh.close()
        writer._fh = None

        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual(wav.getnframes(), 0)  # ヘッダは 0 のまま

        seconds = repair_wav_header(self.path)
        self.assertAlmostEqual(seconds, 3.0, places=3)
        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual(wav.getnframes(), 3 * SAMPLE_RATE)

    def test_reopening_appends_instead_of_truncating(self):
        writer = CrashSafeWavWriter(self.path)
        writer.append(tone(SAMPLE_RATE))
        writer.close()

        resumed = CrashSafeWavWriter(self.path)
        resumed.append(tone(SAMPLE_RATE))
        resumed.close()

        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual(wav.getnframes(), 2 * SAMPLE_RATE)

    def test_repair_on_missing_or_tiny_file_is_safe(self):
        self.assertEqual(repair_wav_header(self.dir / "nope.wav"), 0.0)
        stub = self.dir / "stub.wav"
        stub.write_bytes(b"RIFF")
        self.assertEqual(repair_wav_header(stub), 0.0)


class AsyncWavAppenderTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bridgelog_wavasync_"))
        self.path = self.dir / "recording.wav"

    def test_frames_reach_disk_and_memory_stays_bounded(self):
        appender = AsyncWavAppender(CrashSafeWavWriter(self.path), max_queue=64)
        frame = tone(2048)
        for _ in range(400):
            appender.append(frame)
        appender.close()

        with wave.open(str(self.path), "rb") as wav:
            written = wav.getnframes()
        # キュー上限に当たった分は落ちるが、落ちた事実は必ず数えられている。
        self.assertEqual(written + appender.dropped_frames * 2048, 400 * 2048)
        self.assertIsNone(appender.error)

    def test_append_after_close_is_ignored(self):
        appender = AsyncWavAppender(CrashSafeWavWriter(self.path))
        appender.append(tone(1024))
        appender.close()
        appender.append(tone(1024))  # 例外を出さない
        with wave.open(str(self.path), "rb") as wav:
            self.assertEqual(wav.getnframes(), 1024)


if __name__ == "__main__":
    unittest.main()
