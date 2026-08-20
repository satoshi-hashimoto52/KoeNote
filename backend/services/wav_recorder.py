"""録音音声を文字起こしとは別系統で保存する、クラッシュ耐性のある WAV ライタ。

stdlib の ``wave`` は close() のときにしか RIFF/data のサイズ欄を書かないため、
プロセスが強制終了されるとサイズ 0 のまま残り、多くのプレイヤーが再生を拒否する。
そこでヘッダを自前で書き、一定量ごとにサイズ欄だけを seek して書き戻す。
"""
import os
import struct
import threading
from pathlib import Path
from typing import Optional

from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE

RIFF_HEADER_SIZE = 44
# この量ごとにサイズ欄を書き戻して fsync する。強制終了時の欠損はここまでに収まる。
DEFAULT_SYNC_INTERVAL_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * 10  # 10 秒


def _wav_header(data_bytes: int, sample_rate: int, channels: int) -> bytes:
    byte_rate = sample_rate * channels * BYTES_PER_SAMPLE
    block_align = channels * BYTES_PER_SAMPLE
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


def repair_wav_header(path) -> float:
    """実ファイル長からサイズ欄を再計算する。強制終了後の復旧に使う。返り値は秒数。"""
    target = Path(path)
    if not target.is_file():
        return 0.0
    size = target.stat().st_size
    if size < RIFF_HEADER_SIZE:
        return 0.0
    with open(target, "r+b") as fh:
        head = fh.read(RIFF_HEADER_SIZE)
        sample_rate = struct.unpack("<I", head[24:28])[0] or SAMPLE_RATE
        channels = struct.unpack("<H", head[22:24])[0] or 1
        data_bytes = size - RIFF_HEADER_SIZE
        fh.seek(0)
        fh.write(_wav_header(data_bytes, sample_rate, channels))
        fh.flush()
        os.fsync(fh.fileno())
    return data_bytes / float(sample_rate * channels * BYTES_PER_SAMPLE)


class CrashSafeWavWriter:
    """PCM16LE mono を追記し、定期的にサイズ欄を書き戻す WAV ライタ。"""

    def __init__(
        self,
        path,
        sample_rate: int = SAMPLE_RATE,
        channels: int = 1,
        sync_interval_bytes: int = DEFAULT_SYNC_INTERVAL_BYTES,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sync_interval_bytes = max(int(sync_interval_bytes), BYTES_PER_SAMPLE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data_bytes = 0
        self._synced_bytes = 0

        existing = self.path.is_file() and self.path.stat().st_size >= RIFF_HEADER_SIZE
        self._fh = open(self.path, "r+b" if existing else "w+b")
        if existing:
            # 再接続で同じセッションに戻ってきた場合は末尾へ追記する。
            self._data_bytes = max(0, self.path.stat().st_size - RIFF_HEADER_SIZE)
            self._fh.seek(RIFF_HEADER_SIZE + self._data_bytes)
            self._rewrite_sizes_locked()
        else:
            self._fh.write(_wav_header(0, self.sample_rate, self.channels))
            self._fh.flush()

    @property
    def data_bytes(self) -> int:
        with self._lock:
            return self._data_bytes

    @property
    def recorded_seconds(self) -> float:
        return self.data_bytes / float(self.sample_rate * self.channels * BYTES_PER_SAMPLE)

    def append(self, pcm: bytes) -> None:
        if not pcm or self._fh is None:
            return
        with self._lock:
            self._fh.write(pcm)
            self._data_bytes += len(pcm)
            if self._data_bytes - self._synced_bytes >= self.sync_interval_bytes:
                self._rewrite_sizes_locked()

    def _rewrite_sizes_locked(self) -> None:
        position = self._fh.tell()
        self._fh.seek(0)
        self._fh.write(_wav_header(self._data_bytes, self.sample_rate, self.channels))
        self._fh.seek(position)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._synced_bytes = self._data_bytes

    def close(self) -> None:
        with self._lock:
            if self._fh is None:
                return
            try:
                self._rewrite_sizes_locked()
            finally:
                self._fh.close()
                self._fh = None


class AsyncWavAppender:
    """WAV 書き込みを専用スレッドへ逃がすラッパ。

    asyncio の event loop で fsync するとハートビートまで止まるため、
    ディスク I/O はループスレッドから完全に切り離す。
    """

    def __init__(self, writer: CrashSafeWavWriter, max_queue: int = 4096) -> None:
        self._writer = writer
        self._queue: list[bytes] = []
        self._cond = threading.Condition()
        self._closed = False
        self.max_queue = max_queue
        self.dropped_frames = 0
        self.error: Optional[str] = None
        # 書き込み遅延の警告をユーザーへ出したかどうか（毎窓通知しないため）。
        self.drop_reported = False
        self._thread = threading.Thread(target=self._run, name="wav-appender", daemon=True)
        self._thread.start()

    @property
    def queue_depth(self) -> int:
        with self._cond:
            return len(self._queue)

    @property
    def recorded_seconds(self) -> float:
        return self._writer.recorded_seconds

    @property
    def path(self) -> str:
        return str(self._writer.path)

    def append(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._cond:
            if self._closed:
                return
            if len(self._queue) >= self.max_queue:
                # ディスクが詰まっても録音自体は続ける。落とした事実は必ず報告する。
                self.dropped_frames += 1
                return
            self._queue.append(pcm)
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if not self._queue and self._closed:
                    return
                batch = self._queue
                self._queue = []
            try:
                self._writer.append(b"".join(batch))
            except OSError as exc:
                self.error = str(exc)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()
        self._thread.join(timeout=10.0)
        self._writer.close()
