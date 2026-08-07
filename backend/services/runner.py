import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from .exporter import create_output_directory, export_transcription_files
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from exporter import create_output_directory, export_transcription_files

try:
    from .file_utils import write_text_file
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from file_utils import write_text_file

import torch
import whisper


TERMS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "transcription_terms.json"
FILLER_PATTERN = re.compile(r"(?<![ぁ-んァ-ン一-龥])(?:えー|えっと|あの|その|まあ)(?![ぁ-んァ-ン一-龥])")
JAPANESE_PATTERN = re.compile(r"(?<=[ぁ-んァ-ン一-龥])\s+(?=[ぁ-んァ-ン一-龥])")
ENGLISH_SPACE_PATTERN = re.compile(r"\s+([、。，．！？])")
SENTENCE_ENDINGS = "。！？!?"


def pick_device():
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model(model_spec, device):
    use_fp16 = device != "cpu"
    try:
        model = whisper.load_model(model_spec, device=device)
        return model, device, use_fp16
    except NotImplementedError:
        device = "cpu"
        use_fp16 = False
        model = whisper.load_model(model_spec, device=device)
        return model, device, use_fp16


def resolve_model_spec(raw_model: str) -> str:
    value = (raw_model or "").strip()
    if not value:
        raise ValueError("モデル指定が空です。")
    expanded = Path(value).expanduser()
    if expanded.exists():
        if not expanded.is_file():
            raise ValueError(f"モデル指定がファイルではありません: {expanded}")
        return str(expanded.resolve())
    if any(sep in value for sep in (os.sep, "/", "\\")):
        raise FileNotFoundError(str(expanded))
    return value


def load_terms_config() -> dict:
    try:
        with TERMS_CONFIG_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return {
            "initial_prompt": str(payload.get("initial_prompt", "") or ""),
            "replacements": {
                str(key): str(value)
                for key, value in (payload.get("replacements", {}) or {}).items()
            },
            "remove_fillers": bool(payload.get("remove_fillers", False)),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # 設定ファイルが壊れていても、音声認識自体は継続できるよう既定値へ戻す。
        return {"initial_prompt": "", "replacements": {}, "remove_fillers": False}


def _segment_text(segment) -> str:
    if isinstance(segment, dict):
        return str(segment.get("text", "") or "").strip()
    return str(getattr(segment, "text", "") or "").strip()


def _segment_value(segment, name):
    if isinstance(segment, dict):
        return segment.get(name)
    return getattr(segment, name, None)


def build_raw_text(segments) -> str:
    return "\n".join(text for text in (_segment_text(seg) for seg in segments) if text).strip()


def _format_timestamp(seconds) -> str:
    total = max(0, int(float(seconds or 0)))
    minutes, seconds = divmod(total, 60)
    return f"{minutes:02}:{seconds:02}"


def build_timestamped_text(segments) -> str:
    lines = []
    for segment in segments:
        text = _segment_text(segment)
        if text:
            timestamp = f"[{_format_timestamp(_segment_value(segment, 'start'))} --> {_format_timestamp(_segment_value(segment, 'end'))}]"
            lines.append(f"{timestamp} {text}")
    return "\n".join(lines).strip()


def normalize_terminology(text: str, replacements: dict[str, str]) -> str:
    normalized = text
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(source, target)
    return normalized


def _suppress_duplicate_phrases(text: str) -> str:
    words = text.split()
    if len(words) >= 2 and words[-1] == words[-2]:
        words.pop()
    return " ".join(words)


def format_japanese_transcription(text: str, remove_fillers: bool = False) -> str:
    formatted = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    formatted = JAPANESE_PATTERN.sub("", formatted)
    formatted = ENGLISH_SPACE_PATTERN.sub(r"\1", formatted)
    if remove_fillers:
        formatted = FILLER_PATTERN.sub("", formatted)
    formatted = _suppress_duplicate_phrases(formatted)
    formatted = re.sub(r"\n+", " ", formatted)

    sentences = []
    for part in re.split(r"(?<=[。！？!?])\s*", formatted):
        part = part.strip()
        if not part:
            continue
        if part[-1] not in SENTENCE_ENDINGS and not part.endswith(("。", "？", "！")):
            part += "。"
        sentences.append(part)

    paragraphs = []
    current = []
    current_length = 0
    for sentence in sentences:
        current.append(sentence)
        current_length += len(sentence)
        if len(current) >= 3 or current_length >= 180:
            paragraphs.append("".join(current))
            current = []
            current_length = 0
    if current:
        paragraphs.append("".join(current))
    return "\n\n".join(paragraphs).strip()


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def build_segment_records(segments, replacements):
    records = []
    for segment in segments:
        raw_text = _segment_text(segment)
        record = {
            "start": _json_safe(_segment_value(segment, "start")),
            "end": _json_safe(_segment_value(segment, "end")),
            "raw_text": raw_text,
            "normalized_text": normalize_terminology(raw_text, replacements),
        }
        for name in ("avg_logprob", "no_speech_prob", "compression_ratio", "tokens"):
            value = _segment_value(segment, name)
            if value is not None:
                record[name] = _json_safe(value)
        records.append(record)
    return records


def _transcribe_with_model(model, input_path: str, mode: str, model_spec: str, device: str, use_fp16: bool, terms: dict) -> dict:
    started_at = time.perf_counter()
    transcribe_kwargs = {"language": "ja", "verbose": False, "task": "transcribe", "fp16": use_fp16}
    transcribe_kwargs.update(
        beam_size=3 if mode == "speed" else 5,
        best_of=3 if mode == "speed" else 5,
        temperature=0,
        condition_on_previous_text=True,
        initial_prompt=terms["initial_prompt"],
    )
    result = model.transcribe(input_path, **transcribe_kwargs)
    whisper_segments = result.get("segments", [])
    raw_text = build_raw_text(whisper_segments)
    timestamped_text = build_timestamped_text(whisper_segments)
    normalized_text = normalize_terminology(raw_text, terms["replacements"])
    final_text = format_japanese_transcription(normalized_text, terms["remove_fillers"])
    result = {
        "text": final_text,
        "raw_text": raw_text,
        "timestamped_text": timestamped_text,
        "device": device,
        "mode": mode,
        "model": model_spec,
        "segments": build_segment_records(whisper_segments, terms["replacements"]),
    }
    result["processing_elapsed_seconds"] = round(time.perf_counter() - started_at, 3)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--mode", choices=["speed", "accuracy"], default="speed")
    parser.add_argument("--model", required=True)
    parser.add_argument("--write", default="1")
    parser.add_argument("--output-folder", default="")
    args = parser.parse_args()

    input_paths = [os.path.abspath(path) for path in args.input]
    for input_path in input_paths:
        if not os.path.isfile(input_path):
            raise FileNotFoundError(input_path)
    if len(input_paths) > 1 and str(args.write).lower() in {"1", "true", "on", "yes"}:
        raise ValueError("複数入力ではWorker側のファイル保存を使用できません。")
    model_spec = resolve_model_spec(args.model)
    terms = load_terms_config()
    device = pick_device()
    model, device, use_fp16 = load_model(model_spec, device)
    sys.stderr.write(f"[diagnostic] model_loaded count=1 model={model_spec} device={device}\n")
    results = []
    for input_path in input_paths:
        results.append(_transcribe_with_model(model, input_path, args.mode, model_spec, device, use_fp16, terms))

    if len(results) > 1:
        sys.stdout.write(json.dumps({"results": results}, ensure_ascii=False))
        return
    result = results[0]
    input_path = input_paths[0]
    job_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_paths = {}
    write_to_file = str(args.write).lower() in {"1", "true", "on", "yes"}
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_path": input_path,
        "model": model_spec,
        "mode": args.mode,
        "device": device,
        "language": "ja",
    }
    if write_to_file:
        output_directory, actual_timestamp = create_output_directory(args.output_folder, input_path, job_timestamp)
        job_timestamp = actual_timestamp
        output_paths = export_transcription_files(
            output_directory, job_timestamp, result["text"], result["raw_text"], result["timestamped_text"], result["segments"], metadata
        )

    result.update({
        "output_directory": str(Path(output_paths["output_path"]).parent) if output_paths else None,
        "output_path": output_paths.get("output_path"),
        "raw_output_path": output_paths.get("raw_output_path"),
        "timestamped_output_path": output_paths.get("timestamped_output_path"),
        "segments_output_path": output_paths.get("segments_output_path"),
    })
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(str(exc))
        sys.exit(1)
