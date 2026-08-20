"""realtime 文字起こし用の PCM16LE mono ストリーム基盤。

録音時間に比例してコストが増えないよう、音声は「絶対サンプル番号で addressing する
固定容量リングバッファ」として保持する。総サンプル数だけが単調増加し、
実メモリは capacity で頭打ちになる。
"""
import threading
from typing import Optional

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

# 溢れる度に memmove すると無駄なので、この余裕を超えたときだけまとめて捨てる。
DEFAULT_TRIM_SLACK_SECONDS = 5.0
DEFAULT_CAPACITY_SECONDS = 180.0


class PcmRingBuffer:
    """絶対サンプル番号で読み書きする固定容量 PCM16LE mono バッファ。

    - ``total_samples`` はストリーム開始からの累計。リセットされない。
    - ``read()`` は破棄済み区間を要求されたら ``None`` を返す。黙って別区間を
      返すと「違う音声を違う絶対時刻に紐付ける」ため、正しさのバグになる。
    - スレッドセーフ（受信スレッドと推論スレッドから触られる）。
    """

    def __init__(
        self,
        capacity_seconds: float = DEFAULT_CAPACITY_SECONDS,
        sample_rate: int = SAMPLE_RATE,
        trim_slack_seconds: float = DEFAULT_TRIM_SLACK_SECONDS,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.capacity_samples = max(int(capacity_seconds * self.sample_rate), self.sample_rate)
        self.trim_slack_samples = max(int(trim_slack_seconds * self.sample_rate), 1)
        self._buf = bytearray()
        self._start_sample = 0
        self._total_samples = 0
        self._evicted_samples = 0
        self._lock = threading.Lock()

    # --- 参照系 ---

    @property
    def total_samples(self) -> int:
        with self._lock:
            return self._total_samples

    @property
    def earliest_sample(self) -> int:
        with self._lock:
            return self._start_sample

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return len(self._buf)

    @property
    def evicted_samples(self) -> int:
        with self._lock:
            return self._evicted_samples

    @property
    def capacity_bytes(self) -> int:
        return self.capacity_samples * BYTES_PER_SAMPLE

    # --- 更新系 ---

    def append(self, pcm: bytes) -> int:
        """PCM を追記して累計サンプル数を返す。奇数バイトは切り捨てる。"""
        if not pcm:
            return self.total_samples
        remainder = len(pcm) % BYTES_PER_SAMPLE
        if remainder:
            pcm = pcm[: len(pcm) - remainder]
            if not pcm:
                return self.total_samples
        with self._lock:
            self._buf += pcm
            self._total_samples += len(pcm) // BYTES_PER_SAMPLE
            overflow = len(self._buf) // BYTES_PER_SAMPLE - self.capacity_samples
            if overflow > self.trim_slack_samples:
                del self._buf[: overflow * BYTES_PER_SAMPLE]
                self._start_sample += overflow
                self._evicted_samples += overflow
            return self._total_samples

    def append_silence(self, samples: int) -> int:
        """再接続で失われた区間を無音で埋め、絶対時刻を壁時計に合わせ続ける。"""
        if samples <= 0:
            return self.total_samples
        return self.append(b"\x00" * (samples * BYTES_PER_SAMPLE))

    def read(self, start_sample: int, end_sample: int) -> Optional[bytes]:
        """[start, end) を返す。破棄済み・未到達・空範囲なら None。"""
        with self._lock:
            if end_sample <= start_sample:
                return None
            if start_sample < self._start_sample or end_sample > self._total_samples:
                return None
            a = (start_sample - self._start_sample) * BYTES_PER_SAMPLE
            b = (end_sample - self._start_sample) * BYTES_PER_SAMPLE
            return bytes(self._buf[a:b])


class Resampler16k:
    """任意サンプルレート -> 16kHz の線形補間リサンプラ。

    AudioContext({sampleRate:16000}) がそのまま 16kHz を返さないデバイス用の保険。
    チャンク境界で位相と末尾1サンプルを持ち越し、継ぎ目を作らない。
    """

    def __init__(self, source_rate: int, target_rate: int = SAMPLE_RATE) -> None:
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        self._phase = 0.0
        self._carry = np.zeros(0, dtype=np.float32)

    @property
    def passthrough(self) -> bool:
        return self.source_rate == self.target_rate

    def process(self, pcm: bytes) -> bytes:
        if self.passthrough or not pcm:
            return pcm
        remainder = len(pcm) % BYTES_PER_SAMPLE
        if remainder:
            pcm = pcm[: len(pcm) - remainder]
        if not pcm:
            return b""
        incoming = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        src = np.concatenate((self._carry, incoming)) if self._carry.size else incoming
        if src.size < 2:
            self._carry = src
            return b""
        ratio = self.source_rate / self.target_rate
        # 直前チャンクから持ち越した位相を起点に、補間可能な範囲だけ出力する。
        count = int(np.floor((src.size - 1 - self._phase) / ratio)) + 1
        if count <= 0:
            self._carry = src
            return b""
        positions = self._phase + ratio * np.arange(count, dtype=np.float64)
        out = np.interp(positions, np.arange(src.size, dtype=np.float64), src)
        consumed = int(np.floor(positions[-1]))
        self._phase = positions[-1] - consumed
        self._carry = src[consumed:]
        return np.clip(np.rint(out), -32768, 32767).astype("<i2").tobytes()
