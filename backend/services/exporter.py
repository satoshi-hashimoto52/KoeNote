import json
from pathlib import Path

try:
    from .file_utils import write_text_file
except ImportError:
    from file_utils import write_text_file


def write_segments_json(path: Path, metadata: dict, segments: list[dict]) -> None:
    content = json.dumps({"metadata": metadata, "segments": segments}, ensure_ascii=False, indent=2)
    write_text_file(path, content + "\n")


def create_output_directory(base_output_folder: str, input_path: str, job_timestamp: str) -> tuple[Path, str]:
    base = Path(base_output_folder).expanduser() if base_output_folder.strip() else Path(input_path).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    candidate_name = job_timestamp
    suffix = 1
    while True:
        candidate = base / candidate_name
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate, candidate_name
        except FileExistsError:
            suffix += 1
            candidate_name = f"{job_timestamp}_{suffix:02}"


def export_transcription_files(output_directory: Path, job_timestamp: str, final_text: str, raw_text: str, timestamped_text: str, segments: list[dict], metadata: dict) -> dict:
    files = {
        "output_path": output_directory / f"{job_timestamp}.txt",
        "raw_output_path": output_directory / f"{job_timestamp}_raw.txt",
        "timestamped_output_path": output_directory / f"{job_timestamp}_timestamped.txt",
        "segments_output_path": output_directory / f"{job_timestamp}_segments.json",
    }
    written = []
    failures = []
    for key, path, content in (
        ("output_path", files["output_path"], final_text),
        ("raw_output_path", files["raw_output_path"], raw_text),
        ("timestamped_output_path", files["timestamped_output_path"], timestamped_text),
    ):
        try:
            write_text_file(path, content + ("\n" if content else ""))
            written.append(str(path))
        except OSError as exc:
            failures.append(f"{path}: {exc}")
    try:
        write_segments_json(files["segments_output_path"], metadata, segments)
        written.append(str(files["segments_output_path"]))
    except OSError as exc:
        failures.append(f"{files['segments_output_path']}: {exc}")
    if failures:
        raise OSError(f"出力に失敗しました。成功: {', '.join(written) or 'なし'} / 失敗: {'; '.join(failures)}")
    return {key: str(path) for key, path in files.items()}
