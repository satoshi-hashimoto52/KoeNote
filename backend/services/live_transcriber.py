import os
import math
import shutil
import subprocess
import tempfile
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Dict

from .transcriber import resolve_ffmpeg_dir

SUPPORTED_MODELS = {"tiny", "base", "small", "medium"}
DEFAULT_MODEL = "small"
MIN_RMS = 0.006
HALLUCINATION_PHRASES = {
    "ご視聴ありがとうございました",
    "ご清聴ありがとうございました",
    "ありがとうございました",
    "あっはっは",
    "あはは",
}

_model_cache: Dict[str, object] = {}
_model_lock = threading.Lock()


def _load_model(model_name: str):
    normalized = (model_name or DEFAULT_MODEL).strip().lower()
    if normalized not in SUPPORTED_MODELS:
        raise ValueError(f"未対応のモデルです: {model_name}")

    with _model_lock:
        cached = _model_cache.get(normalized)
        if cached is not None:
            return cached

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper が未インストールです。backend/requirements.txt を再インストールしてください。"
            ) from exc

        model = WhisperModel(normalized, device="cpu", compute_type="int8")
        _model_cache[normalized] = model
        return model


def _resolve_ffmpeg_binary() -> str:
    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None:
        candidate = ffmpeg_dir / "ffmpeg"
        if candidate.is_file():
            return str(candidate)

    system_bin = shutil.which("ffmpeg")
    if system_bin:
        return system_bin

    raise RuntimeError("ffmpeg が見つかりません。")


def _convert_webm_to_wav(input_path: Path, output_path: Path) -> None:
    ffmpeg_bin = _resolve_ffmpeg_binary()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output_path),
    ]
    env = os.environ.copy()
    ffmpeg_dir = resolve_ffmpeg_dir()
    if ffmpeg_dir is not None:
        env["PATH"] = f"{str(ffmpeg_dir)}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(f"音声chunk変換に失敗しました。{detail}".strip())


def _calculate_wav_rms(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
        if sample_width != 2 or frame_count <= 0:
            return 0.0
        data = wav.readframes(frame_count)

    if not data:
        return 0.0

    total = 0.0
    samples = len(data) // 2
    for i in range(0, len(data), 2):
        value = int.from_bytes(data[i:i + 2], byteorder="little", signed=True)
        normalized = value / 32768.0
        total += normalized * normalized
    return math.sqrt(total / max(samples, 1))


def _is_hallucination_text(text: str) -> bool:
    normalized = "".join(str(text or "").split())
    if not normalized:
        return False
    return any(phrase in normalized for phrase in HALLUCINATION_PHRASES)


def _debug_chunk_dir() -> Path:
    path = Path(tempfile.gettempdir()) / f"bridgelog_live_debug_{datetime.now().strftime('%Y%m%d')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_debug_file(source: Path, suffix: str) -> str:
    target = _debug_chunk_dir() / f"chunk_{datetime.now().strftime('%H%M%S_%f')}{suffix}"
    shutil.copy2(source, target)
    return str(target)


def transcribe_audio_chunk(audio_bytes: bytes, mime_type: str, model_name: str, debug_save: bool = False) -> dict:
    if not audio_bytes:
        return {
            "text": "",
            "model": model_name or DEFAULT_MODEL,
            "rms": 0.0,
            "skipped": True,
            "skip_reason": "empty_chunk",
            "debug_path": None,
            "debug_wav_path": None,
        }

    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    suffix = ".webm"
    if "mp4" in (mime_type or ""):
        suffix = ".mp4"

    with tempfile.TemporaryDirectory(prefix="bridgelog_live_chunk_") as tmp:
        input_path = Path(tmp) / f"chunk{suffix}"
        wav_path = Path(tmp) / "chunk.wav"
        input_path.write_bytes(audio_bytes)
        _convert_webm_to_wav(input_path, wav_path)
        rms = _calculate_wav_rms(wav_path)
        debug_path = _save_debug_file(input_path, suffix) if debug_save else None
        debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None

        if rms < MIN_RMS:
            return {
                "text": "",
                "model": model_key,
                "rms": rms,
                "skipped": True,
                "skip_reason": f"low_rms<{MIN_RMS}",
                "debug_path": debug_path,
                "debug_wav_path": debug_wav_path,
            }

        model = _load_model(model_key)
        segments, _info = model.transcribe(
            str(wav_path),
            language="ja",
            beam_size=3,
            vad_filter=True,
            temperature=0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        accepted_texts = []
        accepted_segments = []
        dropped_reasons = []
        for segment in segments:
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            no_speech_prob = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
            avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
            compression_ratio = float(getattr(segment, "compression_ratio", 0.0) or 0.0)
            if no_speech_prob > 0.6:
                dropped_reasons.append(f"no_speech_prob={no_speech_prob:.2f}")
                continue
            if avg_logprob < -1.0:
                dropped_reasons.append(f"avg_logprob={avg_logprob:.2f}")
                continue
            if compression_ratio > 2.4:
                dropped_reasons.append(f"compression_ratio={compression_ratio:.2f}")
                continue
            if _is_hallucination_text(text):
                dropped_reasons.append("hallucination_phrase")
                continue
            accepted_texts.append(text)
            accepted_segments.append({
                "start": float(getattr(segment, "start", 0.0) or 0.0),
                "end": float(getattr(segment, "end", 0.0) or 0.0),
                "text": text,
            })

        final_text = "".join(accepted_texts).strip()
        return {
            "text": final_text,
            "segments": accepted_segments,
            "model": model_key,
            "rms": rms,
            "skipped": not bool(final_text),
            "skip_reason": ", ".join(dropped_reasons) if dropped_reasons else "",
            "debug_path": debug_path,
            "debug_wav_path": debug_wav_path,
        }


def transcribe_wav_file(wav_path: Path, model_name: str, debug_save: bool = False) -> dict:
    model_key = (model_name or DEFAULT_MODEL).strip().lower()
    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        return {
            "text": "",
            "model": model_key,
            "rms": 0.0,
            "skipped": True,
            "skip_reason": "empty_wav",
            "debug_path": None,
            "debug_wav_path": None,
        }

    rms = _calculate_wav_rms(wav_path)
    debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None
    if rms < MIN_RMS:
        return {
            "text": "",
            "model": model_key,
            "rms": rms,
            "skipped": True,
            "skip_reason": f"low_rms<{MIN_RMS}",
            "debug_path": None,
            "debug_wav_path": debug_wav_path,
        }

    model = _load_model(model_key)
    segments, _info = model.transcribe(
        str(wav_path),
        language="ja",
        beam_size=3,
        vad_filter=True,
        temperature=0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
    )
    accepted_texts = []
    accepted_segments = []
    dropped_reasons = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if not text:
            continue
        no_speech_prob = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
        avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
        compression_ratio = float(getattr(segment, "compression_ratio", 0.0) or 0.0)
        if no_speech_prob > 0.6:
            dropped_reasons.append(f"no_speech_prob={no_speech_prob:.2f}")
            continue
        if avg_logprob < -1.0:
            dropped_reasons.append(f"avg_logprob={avg_logprob:.2f}")
            continue
        if compression_ratio > 2.4:
            dropped_reasons.append(f"compression_ratio={compression_ratio:.2f}")
            continue
        if _is_hallucination_text(text):
            dropped_reasons.append("hallucination_phrase")
            continue
        accepted_texts.append(text)
        accepted_segments.append({
            "start": float(getattr(segment, "start", 0.0) or 0.0),
            "end": float(getattr(segment, "end", 0.0) or 0.0),
            "text": text,
        })

    final_text = "".join(accepted_texts).strip()
    return {
        "text": final_text,
        "segments": accepted_segments,
        "model": model_key,
        "rms": rms,
        "skipped": not bool(final_text),
        "skip_reason": ", ".join(dropped_reasons) if dropped_reasons else "",
        "debug_path": None,
        "debug_wav_path": debug_wav_path,
    }


def convert_webm_bytes_to_wav(audio_bytes: bytes, mime_type: str, debug_save: bool = False) -> dict:
    suffix = ".webm"
    if "mp4" in (mime_type or ""):
        suffix = ".mp4"

    with tempfile.TemporaryDirectory(prefix="bridgelog_live_joined_") as tmp:
        input_path = Path(tmp) / f"joined{suffix}"
        wav_path = Path(tmp) / "joined.wav"
        input_path.write_bytes(audio_bytes)
        _convert_webm_to_wav(input_path, wav_path)
        debug_path = _save_debug_file(input_path, suffix) if debug_save else None
        debug_wav_path = _save_debug_file(wav_path, ".wav") if debug_save else None
        return {
            "wav_path": str(wav_path),
            "wav_bytes": wav_path.read_bytes(),
            "debug_path": debug_path,
            "debug_wav_path": debug_wav_path,
        }
