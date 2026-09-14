"""入力モードと入力方式別プリセット（0019）。

デバイス名だけで処理を固定しない。「入力モード」と「デバイス別設定」を組み合わせ、
自動判定の結果は UI へ出してユーザーが上書きできるようにする。

frontend/src/features/audio/inputProfile.ts と同じ表を持つ。
片方だけ変えると UI 表示と実処理がずれるため、必ず両方を更新すること。
"""
import re
from dataclasses import dataclass, asdict
from typing import Optional

from .audio_levels import (
    ABSOLUTE_SILENCE_RMS,
    MAX_GAIN_DB,
    SPEECH_OVER_FLOOR_DB,
    TARGET_SPEECH_DBFS,
)

# --- 入力モード ---------------------------------------------------------------

MODE_AUTO = "auto"
MODE_MIC = "mic"
MODE_LOOPBACK = "loopback"
MODE_CUSTOM = "custom"
INPUT_MODES = (MODE_AUTO, MODE_MIC, MODE_LOOPBACK, MODE_CUSTOM)

# --- ゲインモード -------------------------------------------------------------

GAIN_AUTO = "auto"
GAIN_NONE = "none"
GAIN_MANUAL = "manual"
GAIN_MODES = (GAIN_AUTO, GAIN_NONE, GAIN_MANUAL)

# --- 無音判定モード -----------------------------------------------------------

SILENCE_RELATIVE = "relative"
SILENCE_ABSOLUTE = "absolute"
SILENCE_MANUAL = "manual"
SILENCE_MODES = (SILENCE_RELATIVE, SILENCE_ABSOLUTE, SILENCE_MANUAL)

# 仮想オーディオデバイス（ループバック）の既知の名前。
# 単語境界で見るので "Microphone" のような無関係な語には当たらない。
LOOPBACK_PATTERNS = (
    r"blackhole",
    r"soundflower",
    r"loopback\s*audio",
    r"vb[-\s]?cable",
    r"vb[-\s]?audio",
    r"existential\s*audio",
    r"ishowu",
    r"aggregate\s*device",
    r"multi[-\s]?output",
    r"virtual\s*(audio|cable|input)",
)
_LOOPBACK_RE = re.compile("|".join(LOOPBACK_PATTERNS), re.IGNORECASE)

# 警告までの継続時間の許容範囲。
MIN_WARNING_SECONDS = 5.0
MAX_WARNING_SECONDS = 120.0
DEFAULT_WARNING_SECONDS = 20.0


def detect_mode(device_label: Optional[str]) -> str:
    """デバイス名から入力モードを自動判定する。

    明確に判定できる仮想オーディオデバイスだけ ``loopback``。
    それ以外はすべて ``mic``（判定に自信が無いときにマイク扱いにしておけば、
    補正が効くだけで音は壊れない）。
    """
    label = str(device_label or "").strip()
    if not label:
        return MODE_MIC
    return MODE_LOOPBACK if _LOOPBACK_RE.search(label) else MODE_MIC


def resolve_mode(requested_mode: Optional[str], device_label: Optional[str]) -> tuple[str, str]:
    """(実効モード, 自動判定結果) を返す。

    ``custom`` は実効モードとしてそのまま残す（プリセットではなくユーザー設定を使う）。
    ``auto`` のときだけ自動判定を実効モードに採用する。
    """
    detected = detect_mode(device_label)
    mode = str(requested_mode or MODE_AUTO).strip().lower()
    if mode not in INPUT_MODES:
        mode = MODE_AUTO
    if mode == MODE_AUTO:
        return detected, detected
    return mode, detected


def _clamp(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed:  # NaN
        return float(default)
    return float(max(minimum, min(parsed, maximum)))


@dataclass
class InputProfile:
    """1 セッションで使う入力設定。プリセットとユーザー設定を解決した結果。"""

    mode: str = MODE_MIC
    detected_mode: str = MODE_MIC
    requested_mode: str = MODE_AUTO
    device_label: str = ""
    gain_mode: str = GAIN_AUTO
    manual_gain_db: float = 0.0
    max_gain_db: float = MAX_GAIN_DB
    target_speech_dbfs: float = TARGET_SPEECH_DBFS
    silence_mode: str = SILENCE_RELATIVE
    manual_silence_rms: float = ABSOLUTE_SILENCE_RMS
    speech_over_floor_db: float = SPEECH_OVER_FLOOR_DB
    clip_protection: bool = True
    low_input_warning: bool = True
    low_input_warning_seconds: float = DEFAULT_WARNING_SECONDS

    @property
    def gain_enabled(self) -> bool:
        """ゲインを 1 サンプルでも変更するか。``none`` かつ 0dB なら素通し。"""
        if self.gain_mode == GAIN_NONE:
            return False
        if self.gain_mode == GAIN_MANUAL:
            return abs(self.manual_gain_db) > 1e-9
        return self.max_gain_db > 0.0

    def as_dict(self) -> dict:
        data = asdict(self)
        data["gain_enabled"] = self.gain_enabled
        return data

    def summary(self) -> str:
        """diagnostics.log の 1 行。デバイス名は含めるがそれ以外の個体情報は出さない。"""
        return (
            f"input_profile mode={self.mode} requested={self.requested_mode} "
            f"detected={self.detected_mode} gain_mode={self.gain_mode} "
            f"max_gain_db={self.max_gain_db:.1f} manual_gain_db={self.manual_gain_db:.1f} "
            f"silence_mode={self.silence_mode} clip_protection={int(self.clip_protection)} "
            f"warn={int(self.low_input_warning)} warn_seconds={self.low_input_warning_seconds:.0f}"
        )


# --- プリセット ---------------------------------------------------------------
# 値の根拠は docs/issues/0019「入力方式別プリセット（初期値の根拠）」を参照。

PRESETS: dict[str, dict] = {
    MODE_MIC: {
        # 物理マイク。遠方の会話を拾うため自動補正を効かせる。
        "gain_mode": GAIN_AUTO,
        "manual_gain_db": 0.0,
        "max_gain_db": MAX_GAIN_DB,
        "silence_mode": SILENCE_RELATIVE,
        "manual_silence_rms": ABSOLUTE_SILENCE_RMS,
        "clip_protection": True,
        "low_input_warning": True,
        "low_input_warning_seconds": DEFAULT_WARNING_SECONDS,
    },
    MODE_LOOPBACK: {
        # 内部音声。既にアプリ側で正規化された音が来るので触らない。
        # 無音が厳密なデジタル 0 になりノイズフロアが 0 へ張り付くため、
        # 相対判定ではなく絶対下限だけで判定する。
        "gain_mode": GAIN_NONE,
        "manual_gain_db": 0.0,
        "max_gain_db": 0.0,
        "silence_mode": SILENCE_ABSOLUTE,
        "manual_silence_rms": ABSOLUTE_SILENCE_RMS,
        "clip_protection": True,
        "low_input_warning": True,
        "low_input_warning_seconds": DEFAULT_WARNING_SECONDS,
    },
}


def preset_for(mode: str) -> dict:
    """モードに対応する初期プリセット。``custom``/``auto`` は mic を土台にする。"""
    return dict(PRESETS.get(mode, PRESETS[MODE_MIC]))


def build_profile(payload: Optional[dict], device_label: Optional[str] = None) -> InputProfile:
    """設定 payload と デバイス名から実際に使うプロファイルを組み立てる。

    - 未知のキーは無視する（呼び出し側の設定ファイルは壊さない）
    - 不正値・欠損値は必ずプリセット値へフォールバックする
    - ``custom`` 以外では、ユーザーが個別に指定した値よりプリセットを優先する
      （モードを選び直したときに前のモードの値が残らないようにする）
    """
    data = payload if isinstance(payload, dict) else {}
    label = str(data.get("device_label") or device_label or "").strip()
    requested = str(data.get("mode") or MODE_AUTO).strip().lower()
    if requested not in INPUT_MODES:
        requested = MODE_AUTO
    mode, detected = resolve_mode(requested, label)

    base = preset_for(MODE_MIC if mode == MODE_CUSTOM else mode)

    if mode == MODE_CUSTOM:
        gain_mode = str(data.get("gain_mode") or base["gain_mode"]).strip().lower()
        if gain_mode not in GAIN_MODES:
            gain_mode = base["gain_mode"]
        silence_mode = str(data.get("silence_mode") or base["silence_mode"]).strip().lower()
        if silence_mode not in SILENCE_MODES:
            silence_mode = base["silence_mode"]
        max_gain_db = _clamp(data.get("max_gain_db"), base["max_gain_db"], 0.0, MAX_GAIN_DB)
        manual_gain_db = _clamp(data.get("manual_gain_db"), 0.0, 0.0, MAX_GAIN_DB)
        manual_silence_rms = _clamp(
            data.get("manual_silence_rms"), ABSOLUTE_SILENCE_RMS, 0.0, 0.05
        )
        low_input_warning = bool(data.get("low_input_warning", base["low_input_warning"]))
        warning_seconds = _clamp(
            data.get("low_input_warning_seconds"),
            base["low_input_warning_seconds"],
            MIN_WARNING_SECONDS,
            MAX_WARNING_SECONDS,
        )
    else:
        gain_mode = base["gain_mode"]
        silence_mode = base["silence_mode"]
        max_gain_db = float(base["max_gain_db"])
        manual_gain_db = float(base["manual_gain_db"])
        manual_silence_rms = float(base["manual_silence_rms"])
        # 警告の ON/OFF と待ち時間だけは、モードを問わずユーザー設定を尊重する
        # （うるさいと感じたら切れる、という操作を奪わない）。
        low_input_warning = bool(data.get("low_input_warning", base["low_input_warning"]))
        warning_seconds = _clamp(
            data.get("low_input_warning_seconds"),
            base["low_input_warning_seconds"],
            MIN_WARNING_SECONDS,
            MAX_WARNING_SECONDS,
        )

    return InputProfile(
        mode=mode,
        detected_mode=detected,
        requested_mode=requested,
        device_label=label,
        gain_mode=gain_mode,
        manual_gain_db=manual_gain_db,
        max_gain_db=max_gain_db,
        target_speech_dbfs=TARGET_SPEECH_DBFS,
        silence_mode=silence_mode,
        manual_silence_rms=manual_silence_rms,
        speech_over_floor_db=SPEECH_OVER_FLOOR_DB,
        # クリッピング防止は常に有効。切れるようにする理由が無い。
        clip_protection=True,
        low_input_warning=low_input_warning,
        low_input_warning_seconds=warning_seconds,
    )
