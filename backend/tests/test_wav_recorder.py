import os
import shutil
import struct
import subprocess
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
    RecorderRegistry,
    repair_wav_header,
)


def tone(samples: int) -> bytes:
    return (np.sin(np.arange(samples) * 0.1) * 12000).astype("<i2").tobytes()


class CrashSafeWavWriterTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="koenote_wavtest_"))
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
        self.dir = Path(tempfile.mkdtemp(prefix="koenote_wavasync_"))
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


class StaleRecorderHeaderRollbackTest(unittest.TestCase):
    """0013: 修復済みヘッダを、古い recorder の close が巻き戻さないこと。

    recorder は WebSocket 接続ごとに生成される（routes/whisper.py）。
    再接続が重なると同じ WAV に複数の recorder が並ぶ。
    古い recorder は自分が書いた分しか _data_bytes に持たないため、
    repair_wav_header で実ファイル長へ直した後にその close が走ると
    ヘッダが短い値へ巻き戻り、末尾が再生できなくなる。
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "recording.wav"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _declared_data_bytes(self) -> int:
        with open(self.path, "rb") as fh:
            head = fh.read(RIFF_HEADER_SIZE)
        return struct.unpack("<I", head[40:44])[0]

    def test_old_recorder_close_does_not_shrink_repaired_header(self):
        # 1) 古い recorder が 1.0 秒ぶん書く（close しない = 接続が残っている状態）
        #    sync_interval を小さくして、実運用の「10 秒ごとの書き戻し」済みの状態を作る。
        #    ここを流さないと古い分がバッファに残り、後続 recorder が末尾を見失う。
        old = CrashSafeWavWriter(self.path, sync_interval_bytes=BYTES_PER_SAMPLE)
        old.append(b"\x00" * (SAMPLE_RATE * BYTES_PER_SAMPLE))
        old_declared = old.data_bytes

        # 2) 新しい recorder が同じファイルを開いて追記する
        new = CrashSafeWavWriter(self.path, sync_interval_bytes=BYTES_PER_SAMPLE)
        new.append(b"\x01" * (SAMPLE_RATE * BYTES_PER_SAMPLE // 2))  # +0.5 秒
        new.close()

        actual_data_bytes = self.path.stat().st_size - RIFF_HEADER_SIZE
        self.assertGreater(actual_data_bytes, old_declared)

        # 3) 実ファイル長からヘッダを修復する（停止処理の repair_audio 相当）
        repair_wav_header(self.path)
        self.assertEqual(self._declared_data_bytes(), actual_data_bytes)

        # 4) ここで古い recorder が閉じられる（Backend 終了時に起きる）
        old.close()

        # 修復済みの長さが保たれること。巻き戻ると末尾が再生できなくなる。
        self.assertEqual(
            self._declared_data_bytes(),
            actual_data_bytes,
            "古い recorder の close が修復済みヘッダを巻き戻した（0013）",
        )

    def test_close_never_declares_less_than_actual_file_length(self):
        writer = CrashSafeWavWriter(self.path, sync_interval_bytes=BYTES_PER_SAMPLE)
        writer.append(b"\x00" * 1000)
        # 別経路（別 recorder）が追記した状況を作る。writer の _data_bytes は増えない。
        with open(self.path, "ab") as fh:
            fh.write(b"\x02" * 4000)
        writer.close()
        actual = self.path.stat().st_size - RIFF_HEADER_SIZE
        self.assertEqual(self._declared_data_bytes(), actual)

    def test_close_is_idempotent(self):
        writer = CrashSafeWavWriter(self.path, sync_interval_bytes=BYTES_PER_SAMPLE)
        writer.append(b"\x00" * 320)
        writer.close()
        declared = self._declared_data_bytes()
        writer.close()  # 2 回目
        writer.close()  # 3 回目
        self.assertEqual(self._declared_data_bytes(), declared)

    def test_periodic_sync_still_updates_header(self):
        # 既存の 10 秒ごとのヘッダ更新を壊していないこと
        writer = CrashSafeWavWriter(self.path, sync_interval_bytes=640)
        writer.append(b"\x00" * 1280)
        self.assertEqual(self._declared_data_bytes(), 1280)
        writer.close()


class RecorderOwnershipTest(unittest.TestCase):
    """0013-B: WAV パス単位で書き込み可能な recorder を 1 つに保つ。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "audio" / "recording.wav"
        self.registry = RecorderRegistry()

    def tearDown(self):
        self.registry.close_all()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _declared(self) -> int:
        with open(self.path, "rb") as fh:
            return struct.unpack("<I", fh.read(RIFF_HEADER_SIZE)[40:44])[0]

    def _open_fd_count(self) -> int:
        """このプロセスが対象ファイルを開いている数。"""
        out = subprocess.run(["lsof", "-p", str(os.getpid())],
                             capture_output=True, text=True).stdout
        return sum(1 for line in out.splitlines() if str(self.path) in line)

    # 1) 同じセッションへ 2 接続しても書き込み recorder は 1 つだけ
    def test_second_acquire_leaves_only_one_owner(self):
        first = self.registry.acquire(self.path)
        second = self.registry.acquire(self.path)
        self.assertIsNot(first, second)
        self.assertEqual(len(self.registry), 1)
        self.assertIs(self.registry.owner(self.path), second)

    # 2) 再接続時に旧 recorder の FD が閉じられる
    def test_reacquire_closes_previous_recorder(self):
        first = self.registry.acquire(self.path)
        first.append(b"\x00" * 320)
        self.registry.acquire(self.path)
        self.assertTrue(first.closed, "旧 recorder が閉じられていない")

    # 3) 通常停止後に WAV の FD が残らない
    def test_release_leaves_no_open_fd(self):
        recorder = self.registry.acquire(self.path)
        recorder.append(b"\x00" * 320)
        self.assertGreaterEqual(self._open_fd_count(), 1)
        self.registry.release(recorder)
        self.assertEqual(len(self.registry), 0)
        self.assertEqual(self._open_fd_count(), 0, "停止後も FD が残っている")

    # 4) 停止後の append が拒否される
    def test_append_after_release_is_rejected(self):
        recorder = self.registry.acquire(self.path)
        recorder.append(b"\x00" * 320)
        self.registry.release(recorder)
        size_after_stop = self.path.stat().st_size
        recorder.append(b"\xff" * 320)
        self.assertEqual(self.path.stat().st_size, size_after_stop,
                         "停止後の追記がファイルへ届いている")

    # 5) 遅れて旧 WebSocket の finally が走っても安全
    def test_late_release_of_old_recorder_does_not_touch_new_owner(self):
        old = self.registry.acquire(self.path)
        old.append(b"\x00" * 32000)
        new = self.registry.acquire(self.path)     # 旧はここで閉じられる
        new.append(b"\x01" * 16000)
        # 旧接続の finally が今ごろ走る
        owned = self.registry.release(old)
        self.assertFalse(owned, "旧 recorder が所有者と判定された")
        self.assertIs(self.registry.owner(self.path), new, "新しい所有者が外された")
        self.assertFalse(new.closed, "新しい recorder が巻き込まれて閉じられた")

    # 6) stop / close を複数回呼んでも安全
    def test_release_is_idempotent(self):
        recorder = self.registry.acquire(self.path)
        recorder.append(b"\x00" * 320)
        self.assertTrue(self.registry.release(recorder))
        declared = self._declared()
        self.assertFalse(self.registry.release(recorder))
        recorder.close()
        self.assertEqual(self._declared(), declared)

    # 7) repair 後に Backend 相当の shutdown を行ってもヘッダが変わらない
    def test_shutdown_after_repair_keeps_header(self):
        old = self.registry.acquire(self.path)
        old.append(b"\x00" * 32000)
        new = self.registry.acquire(self.path)
        new.append(b"\x01" * 16000)
        self.registry.release(new)

        repair_wav_header(self.path)
        repaired = self._declared()
        actual = self.path.stat().st_size - RIFF_HEADER_SIZE
        self.assertEqual(repaired, actual)

        self.registry.close_all()   # Backend shutdown 相当
        old.close()                 # 取り残された recorder の close
        self.assertEqual(self._declared(), repaired, "shutdown でヘッダが変化した")

    # 8) 停止後にファイルを排他的に移動できる（ハンドルが残っていない証拠）
    def test_file_can_be_renamed_after_release(self):
        recorder = self.registry.acquire(self.path)
        recorder.append(b"\x00" * 320)
        self.registry.release(recorder)
        moved = self.path.with_name("moved.wav")
        self.path.rename(moved)
        self.assertTrue(moved.is_file())
        self.assertFalse(self.path.exists())
