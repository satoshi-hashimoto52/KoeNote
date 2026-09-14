"""入力レベルの測定と判定（0019）。

固定 ``MIN_RMS`` を撤去するにあたり、レベルに関する判断材料を 1 箇所へ集める。

用語:
    dBFS
        フルスケール（|x| = 1.0）を 0 とした相対値。**物理音圧 dB SPL ではない。**
        UI・診断ログでは必ず "dBFS" と表記する。
    ノイズフロア
        発話が無い区間の定常レベル。オフライン解析ではフレーム RMS の 10%ile、
        ストリーミングでは非対称一極フィルタで追従した値を使う。
    発話フレーム
        ノイズフロアを ``SPEECH_OVER_FLOOR_DB`` だけ上回るフレーム。

窓平均 RMS で無音判定してはいけない（0019 の主原因）。
10 秒窓の平均は発話の合間の無音を含むため、ノイズフロアとの比が小さくなり、
遠方の通常発話がそのまま閾値未満へ落ちる。実測値は Issue #0019 を参照。
判定は必ず「窓内に発話フレームが存在するか」で行う。
"""
import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .pcm_stream import BYTES_PER_SAMPLE, SAMPLE_RATE

# レベル解析の 1 フレーム。frontend の pcmCapture.FRAME_SAMPLES と同じ 2048（128ms）。
LEVEL_FRAME_SAMPLES = 2048

# --- 設計値（根拠は docs/issues/0019 の実測表） ---------------------------------

# 補正の目標発話レベル。救済処理でこの値により 20,114 字を得た。
TARGET_SPEECH_DBFS = -24.0
# 自動補正の上限。救済に必要だった実測値は最大 +19.9 dB。
# これ以上上げるとノイズフロア -61.8 dBFS が目標レベルへ近づきすぎる。
MAX_GAIN_DB = 24.0
# 発話フレーム判定のノイズフロアからの余裕。実測で発話フレーム率 32.5%。
SPEECH_OVER_FLOOR_DB = 9.0
# 無効入力・デジタル無音だけを捨てるための絶対下限。-74 dBFS ≒ 16bit で 6.5 LSB。
# 実測ノイズフロア 0.00081 の 1/4 であり、遠方発話は決して下回らない。
ABSOLUTE_SILENCE_RMS = 0.0002
# クリッピング防止の天井。
CEILING = 0.95
# |x| がこれ以上のサンプルをクリップとみなす。
CLIP_LEVEL = 0.999

# ノイズフロア追従の時定数（非対称。下降を速く / 上昇を遅くして低パーセンタイル相当にする）。
NOISE_FLOOR_DOWN_SECONDS = 2.0
NOISE_FLOOR_UP_SECONDS = 45.0
# ノイズフロアの初期値。無音から始まっても過大なゲインを出さない程度に置く。
NOISE_FLOOR_INITIAL = 0.002

# 判定しきい値。
GOOD_SNR_DB = 15.0
MIN_SNR_DB = 10.0
GOOD_PASS_RATIO = 0.95
USABLE_PASS_RATIO = 0.80
# これを超えるゲインに頼っている場合は「良好」とはしない。
HEAVY_GAIN_DB = 12.0
# クリップ率がこれを超えたら音割れとみなす。
CLIP_RATIO_WARN = 0.001


def db_from_amplitude(value: float) -> float:
    """振幅（0..1）を dBFS へ。0 以下は -inf ではなく下限値を返す。"""
    v = float(value)
    if not math.isfinite(v) or v <= 0.0:
        return -120.0
    return max(-120.0, 20.0 * math.log10(v))


def amplitude_from_db(dbfs: float) -> float:
    """dBFS を振幅へ。"""
    return float(10.0 ** (float(dbfs) / 20.0))


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """PCM16LE を -1.0..1.0 の float32 配列へ。奇数バイトは切り捨てる。"""
    remainder = len(pcm_bytes) % BYTES_PER_SAMPLE
    if remainder:
        pcm_bytes = pcm_bytes[: len(pcm_bytes) - remainder]
    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """float32 を PCM16LE へ。丸めた結果が範囲外にならないよう必ず clip する。"""
    if samples.size == 0:
        return b""
    return np.clip(np.rint(samples * 32768.0), -32768, 32767).astype("<i2").tobytes()


def rms(samples: np.ndarray) -> float:
    """RMS。float64 で積算して長い窓でも精度を落とさない。"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))


def frame_rms(samples: np.ndarray, frame_samples: int = LEVEL_FRAME_SAMPLES) -> np.ndarray:
    """フレームごとの RMS。端数フレームは捨てる（レベル統計を歪めないため）。"""
    if frame_samples <= 0 or samples.size < frame_samples:
        return np.zeros(0, dtype=np.float64)
    count = samples.size // frame_samples
    block = samples[: count * frame_samples].astype(np.float64).reshape(count, frame_samples)
    return np.sqrt(np.mean(np.square(block), axis=1))


def smoothing_alpha(time_constant_seconds: float, frame_samples: int, sample_rate: int) -> float:
    """一極フィルタの係数。時定数 <= 0 なら平滑化しない（alpha=0）。"""
    if time_constant_seconds <= 0:
        return 0.0
    dt = float(frame_samples) / float(sample_rate)
    return float(math.exp(-dt / float(time_constant_seconds)))


class NoiseFloorTracker:
    """ノイズフロアを非対称一極フィルタで追う。

    下降を速く（2 秒）／上昇を遅く（45 秒）することで、実質的に低パーセンタイルを
    追いかける。単純な最小値追従は一度沈むと復帰できず、実測で -75 dBFS まで
    下振れして発話判定が壊れたため使わない。
    """

    def __init__(
        self,
        down_seconds: float = NOISE_FLOOR_DOWN_SECONDS,
        up_seconds: float = NOISE_FLOOR_UP_SECONDS,
        initial: float = NOISE_FLOOR_INITIAL,
        frame_samples: int = LEVEL_FRAME_SAMPLES,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._a_down = smoothing_alpha(down_seconds, frame_samples, sample_rate)
        self._a_up = smoothing_alpha(up_seconds, frame_samples, sample_rate)
        self.value = float(initial)

    def push(self, frame_level: float) -> float:
        level = max(0.0, float(frame_level))
        alpha = self._a_down if level < self.value else self._a_up
        self.value = self.value * alpha + level * (1.0 - alpha)
        # 完全なデジタル無音（BlackHole 等）で 0 に張り付くと相対判定が成立しない。
        # 下限を絶対下限に合わせ、相対判定側は別途 loopback を絶対判定に切り替える。
        self.value = max(self.value, 0.0)
        return self.value

    def speech_threshold(self, over_floor_db: float = SPEECH_OVER_FLOOR_DB) -> float:
        """発話フレームとみなす下限レベル。"""
        return max(self.value * amplitude_from_db(over_floor_db), ABSOLUTE_SILENCE_RMS)


@dataclass
class LevelStats:
    """一定量の PCM に対するオフライン統計。キャリブレーションと診断で使う。

    パーセンタイルと継続時間で判断する。最大値・最小値だけでは判定しない
    （瞬間ノイズと完全無音に引きずられるため）。
    """

    sample_count: int
    duration_seconds: float
    frame_count: int
    noise_floor: float
    speech_threshold: float
    speech_frame_count: int
    speech_ratio: float
    speech_median: float
    speech_low: float
    peak: float
    overall_rms: float
    clipped_samples: int
    clip_ratio: float
    # フレーム RMS の 95%ile。発話フレームが 1 つも検出できないときの代替。
    frame_high: float = 0.0

    @property
    def effective_speech_level(self) -> float:
        """発話レベルの代表値。

        SNR が低い / 入力が絶対下限を割るほど小さいと発話フレームを 1 つも
        検出できない。そこで「発話が無い」と切り捨てず、フレーム RMS の
        95%ile を代替として使う。こうしないと `noisy` と `too_quiet` が
        すべて `no_input` に潰れ、ユーザーに原因を返せなくなる。
        """
        if self.speech_frame_count > 0 and self.speech_median > 0.0:
            return self.speech_median
        return self.frame_high

    @property
    def snr_db(self) -> float:
        """発話レベル代表値 − ノイズフロア。どちらも測れなければ 0。"""
        level = self.effective_speech_level
        if level <= 0.0 or self.noise_floor <= 0.0:
            return 0.0
        return db_from_amplitude(level) - db_from_amplitude(self.noise_floor)

    def as_dict(self) -> dict:
        return {
            "duration_seconds": round(self.duration_seconds, 3),
            "frame_count": self.frame_count,
            "noise_floor_dbfs": round(db_from_amplitude(self.noise_floor), 1),
            "speech_threshold_dbfs": round(db_from_amplitude(self.speech_threshold), 1),
            "speech_frame_count": self.speech_frame_count,
            "speech_ratio": round(self.speech_ratio, 4),
            "speech_median_dbfs": round(db_from_amplitude(self.speech_median), 1),
            "frame_high_dbfs": round(db_from_amplitude(self.frame_high), 1),
            "speech_low_dbfs": round(db_from_amplitude(self.speech_low), 1),
            "peak_dbfs": round(db_from_amplitude(self.peak), 1),
            "overall_rms_dbfs": round(db_from_amplitude(self.overall_rms), 1),
            "snr_db": round(self.snr_db, 1),
            "clipped_samples": self.clipped_samples,
            "clip_ratio": round(self.clip_ratio, 6),
        }


def analyze_levels(
    samples: np.ndarray,
    *,
    frame_samples: int = LEVEL_FRAME_SAMPLES,
    sample_rate: int = SAMPLE_RATE,
    noise_floor: Optional[float] = None,
    over_floor_db: float = SPEECH_OVER_FLOOR_DB,
) -> LevelStats:
    """PCM 全体のレベル統計を求める。

    ``noise_floor`` を渡すと、その値を基準に発話フレームを判定する
    （環境音測定ステップの結果を発話測定ステップへ持ち込むため）。
    渡さない場合はフレーム RMS の 10%ile を使う。
    """
    frames = frame_rms(samples, frame_samples)
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    clipped = int(np.count_nonzero(np.abs(samples) >= CLIP_LEVEL)) if samples.size else 0

    if frames.size == 0:
        floor = float(noise_floor if noise_floor is not None else 0.0)
        return LevelStats(
            sample_count=int(samples.size),
            duration_seconds=samples.size / float(sample_rate),
            frame_count=0,
            noise_floor=floor,
            speech_threshold=max(floor * amplitude_from_db(over_floor_db), ABSOLUTE_SILENCE_RMS),
            speech_frame_count=0,
            speech_ratio=0.0,
            speech_median=0.0,
            speech_low=0.0,
            peak=peak,
            overall_rms=rms(samples),
            clipped_samples=clipped,
            clip_ratio=(clipped / samples.size) if samples.size else 0.0,
            frame_high=0.0,
        )

    floor = float(noise_floor) if noise_floor is not None else float(np.percentile(frames, 10))
    threshold = max(floor * amplitude_from_db(over_floor_db), ABSOLUTE_SILENCE_RMS)
    speech = frames[frames > threshold]

    return LevelStats(
        sample_count=int(samples.size),
        duration_seconds=samples.size / float(sample_rate),
        frame_count=int(frames.size),
        noise_floor=floor,
        speech_threshold=threshold,
        speech_frame_count=int(speech.size),
        speech_ratio=float(speech.size) / float(frames.size),
        speech_median=float(np.median(speech)) if speech.size else 0.0,
        speech_low=float(np.percentile(speech, 20)) if speech.size else 0.0,
        peak=peak,
        overall_rms=rms(samples),
        clipped_samples=clipped,
        clip_ratio=(clipped / samples.size) if samples.size else 0.0,
        frame_high=float(np.percentile(frames, 95)),
    )


def recommended_gain_db(
    stats: LevelStats,
    *,
    target_dbfs: float = TARGET_SPEECH_DBFS,
    max_gain_db: float = MAX_GAIN_DB,
) -> float:
    """発話レベル中央値を目標へ持ち上げるゲイン。上限で頭打ちにする。

    発話が検出できないときは 0 dB（補正しても意味がないため）。
    """
    level = stats.effective_speech_level
    if level <= 0.0:
        return 0.0
    needed = target_dbfs - db_from_amplitude(level)
    return float(max(0.0, min(needed, max_gain_db)))


def required_gain_db(stats: LevelStats, *, target_dbfs: float = TARGET_SPEECH_DBFS) -> float:
    """上限で頭打ちにしない、本来必要なゲイン。判定に使う。"""
    level = stats.effective_speech_level
    if level <= 0.0:
        return 0.0
    return float(max(0.0, target_dbfs - db_from_amplitude(level)))


def estimate_pass_ratio(
    stats: LevelStats,
    gain_db: float,
    *,
    threshold_rms: float,
) -> float:
    """補正後に「文字起こしへ回るレベル」へ達する発話フレームの割合。

    発話フレームが 0 のときは 0 を返す（判定側で「入力なし」と扱う）。
    ``speech_low``（20%ile）と ``speech_median`` の 2 点から線形に近似せず、
    しきい値と発話レベルの比較で単調に評価する。
    """
    if stats.effective_speech_level <= 0.0:
        return 0.0
    gain = amplitude_from_db(gain_db)
    # 発話フレームが検出できないときは代表値から保守的に見積もる。
    low = (stats.speech_low if stats.speech_frame_count else stats.frame_high * 0.7) * gain
    median = stats.effective_speech_level * gain
    if low >= threshold_rms:
        return 1.0
    if median < threshold_rms:
        # 中央値すら届かない = 半分未満。低い側の分布までは分からないので 0.5 未満に丸める。
        return 0.0 if median <= 0 else max(0.0, min(0.4, 0.4 * (median / threshold_rms)))
    # 20%ile が届かず中央値が届く = 20〜100% の間。線形補間で概算する。
    span = median - low
    if span <= 0:
        return 0.8
    return float(min(1.0, 0.2 + 0.8 * ((median - threshold_rms) / span)))


# 総合判定コード。UI 文言は frontend 側に持つ。
VERDICT_GOOD = "good"
VERDICT_USABLE = "usable"
VERDICT_NEEDS_ADJUST = "needs_adjust"
VERDICT_TOO_QUIET = "too_quiet"
VERDICT_NOISY = "noisy"
VERDICT_CLIPPING = "clipping"
VERDICT_NO_INPUT = "no_input"


def classify_verdict(
    stats: LevelStats,
    *,
    pass_ratio: float,
    required_db: float,
    max_gain_db: float = MAX_GAIN_DB,
) -> str:
    """総合判定。深刻な状態から順に評価する。"""
    # 「入力を検出できない」は、信号そのものが絶対下限を割っている場合だけ。
    # 発話フレームが 0 でも、環境音が大きい / 極端に音が小さいだけのことがあり、
    # それらは noisy / too_quiet として原因を返さなければならない。
    if stats.peak < ABSOLUTE_SILENCE_RMS or stats.effective_speech_level <= 0.0:
        return VERDICT_NO_INPUT
    if stats.clip_ratio > CLIP_RATIO_WARN:
        return VERDICT_CLIPPING
    if stats.snr_db < MIN_SNR_DB:
        return VERDICT_NOISY
    if required_db > max_gain_db:
        return VERDICT_TOO_QUIET
    if pass_ratio < USABLE_PASS_RATIO or stats.snr_db < GOOD_SNR_DB:
        return VERDICT_NEEDS_ADJUST
    if pass_ratio >= GOOD_PASS_RATIO and required_db <= HEAVY_GAIN_DB:
        return VERDICT_GOOD
    return VERDICT_USABLE


def window_has_speech(
    frames: Sequence[float],
    threshold: float,
    *,
    min_frames: int = 1,
) -> bool:
    """窓に発話フレームが含まれるか。

    **窓平均 RMS では判定しない。** 10 秒窓の平均は発話の合間の無音を含むため、
    相対化してもスキップ率 85.7%（実測）に達し、0019 の原因を再現してしまう。
    """
    if min_frames <= 0:
        return True
    hit = 0
    for level in frames:
        if level > threshold:
            hit += 1
            if hit >= min_frames:
                return True
    return False
