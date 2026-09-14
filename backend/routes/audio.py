"""入力テスト（キャリブレーション）とプリセット参照のエンドポイント（0019）。

frontend が録った生 PCM をここへ送り、**録音時と同じコード**で評価する。
UI 側に判定ロジックを二重実装すると、テスト結果と実際の挙動がずれる。

このエンドポイントは会議セッションを一切作らない。
受け取った PCM はメモリ上でのみ扱い、ファイルへ保存しない。
"""
import base64
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import calibration
from services.audio_levels import (
    ABSOLUTE_SILENCE_RMS,
    MAX_GAIN_DB,
    SPEECH_OVER_FLOOR_DB,
    TARGET_SPEECH_DBFS,
)
from services.input_profile import (
    DEFAULT_WARNING_SECONDS,
    GAIN_MODES,
    INPUT_MODES,
    MAX_WARNING_SECONDS,
    MIN_WARNING_SECONDS,
    PRESETS,
    SILENCE_MODES,
    detect_mode,
)
from services.pcm_stream import SAMPLE_RATE

router = APIRouter(prefix="/api/audio")

# 入力テストで受け取る PCM の上限。18 秒 * 16kHz * 2byte = 576KB。
# 事故で巨大なデータが飛んできてもメモリを食い潰さないよう余裕を見て 5MB。
MAX_PCM_BYTES = 5 * 1024 * 1024


class AnalyzeRequest(BaseModel):
    """入力テストの測定結果。PCM16LE mono を base64 で受け取る。"""

    speech_pcm: str
    noise_pcm: Optional[str] = None
    device_label: str = ""
    input_profile: Optional[dict] = None
    sample_rate: int = SAMPLE_RATE


def _decode(field: str, value: Optional[str]) -> bytes:
    if not value:
        return b""
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field} を base64 として読めません")
    if len(raw) > MAX_PCM_BYTES:
        raise HTTPException(status_code=400, detail=f"{field} が大きすぎます（上限 {MAX_PCM_BYTES} バイト）")
    return raw


@router.get("/presets")
def presets():
    """入力方式別プリセットと設計値。UI の初期値・説明文の根拠として使う。"""
    return {
        "unit": "dBFS",
        "input_modes": list(INPUT_MODES),
        "gain_modes": list(GAIN_MODES),
        "silence_modes": list(SILENCE_MODES),
        "presets": PRESETS,
        "limits": {
            "max_gain_db": MAX_GAIN_DB,
            "target_speech_dbfs": TARGET_SPEECH_DBFS,
            "speech_over_floor_db": SPEECH_OVER_FLOOR_DB,
            "absolute_silence_rms": ABSOLUTE_SILENCE_RMS,
            "min_warning_seconds": MIN_WARNING_SECONDS,
            "max_warning_seconds": MAX_WARNING_SECONDS,
            "default_warning_seconds": DEFAULT_WARNING_SECONDS,
        },
        "calibration": {
            "noise_step_seconds": calibration.NOISE_STEP_SECONDS,
            "speech_step_seconds": calibration.SPEECH_STEP_SECONDS,
            "test_sentence": calibration.TEST_SENTENCE,
            "transcribable_dbfs": calibration.TRANSCRIBABLE_DBFS,
        },
    }


@router.get("/detect_mode")
def detect(device_label: str = ""):
    """デバイス名から入力モードを自動判定する。UI の表示用。"""
    return {"device_label": device_label, "detected_mode": detect_mode(device_label)}


@router.post("/analyze")
def analyze(payload: AnalyzeRequest):
    """入力テストの結果を算出する。会議セッションは作らず、PCM も保存しない。"""
    speech = _decode("speech_pcm", payload.speech_pcm)
    if not speech:
        raise HTTPException(status_code=400, detail="speech_pcm が空です")
    noise = _decode("noise_pcm", payload.noise_pcm)
    sample_rate = int(payload.sample_rate or SAMPLE_RATE)
    if sample_rate < 8000 or sample_rate > 192000:
        raise HTTPException(status_code=400, detail="sample_rate が範囲外です")

    return calibration.analyze_calibration(
        speech,
        noise_pcm=noise or None,
        device_label=payload.device_label or "",
        profile_payload=payload.input_profile,
        sample_rate=sample_rate,
    )
