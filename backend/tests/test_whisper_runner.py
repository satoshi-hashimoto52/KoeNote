import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "services" / "runner.py"


def load_runner():
    torch = types.ModuleType("torch")
    torch.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: False)
    )
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    whisper = types.ModuleType("whisper")
    whisper.load_model = lambda *args, **kwargs: None
    sys.modules.setdefault("torch", torch)
    sys.modules.setdefault("whisper", whisper)
    spec = importlib.util.spec_from_file_location("test_whisper_runner_module", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


class WhisperRunnerTest(unittest.TestCase):
    def test_write_text_file_creates_parent_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "nested" / "結果.txt"
            runner.write_text_file(target, "日本語の結果")

            self.assertEqual(target.read_text(encoding="utf-8"), "日本語の結果")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_write_text_file_keeps_completed_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "結果.txt"
            target.write_text("既存の結果", encoding="utf-8")
            with patch.object(runner.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    runner.write_text_file(target, "新しい結果")

            self.assertEqual(target.read_text(encoding="utf-8"), "既存の結果")
            self.assertEqual(list(target.parent.glob(f".{target.name}.*.tmp")), [])

    def test_format_and_terminology(self):
        raw = "今回は クロードコード の機能です"
        normalized = runner.normalize_terminology(raw, {"クロードコード": "Claude Code"})
        self.assertEqual(runner.format_japanese_transcription(normalized), "今回は Claude Code の機能です。")

    def test_output_files_use_one_timestamp_and_keep_raw_text(self):
        segments = [
            {"start": 0.0, "end": 5.2, "text": "今回はクロードコードの機能です", "avg_logprob": -0.1, "tokens": [1, 2]},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output, actual_name = runner.create_output_directory(temp_dir, "/input/example.mp4", "20260723_163025")
            paths = runner.export_transcription_files(
                output,
                actual_name,
                "今回はClaude Codeの機能です。",
                runner.build_raw_text(segments),
                runner.build_timestamped_text(segments),
                runner.build_segment_records(segments, {"クロードコード": "Claude Code"}),
                {"language": "ja"},
            )
            self.assertEqual(actual_name, "20260723_163025")
            self.assertEqual(Path(paths["output_path"]).name, "20260723_163025.txt")
            self.assertEqual(Path(paths["raw_output_path"]).name, "20260723_163025_raw.txt")
            self.assertEqual(Path(paths["timestamped_output_path"]).name, "20260723_163025_timestamped.txt")
            self.assertEqual(Path(paths["segments_output_path"]).name, "20260723_163025_segments.json")
            self.assertEqual(Path(paths["raw_output_path"]).read_text(encoding="utf-8"), "今回はクロードコードの機能です\n")
            payload = json.loads(Path(paths["segments_output_path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["segments"][0]["raw_text"], "今回はクロードコードの機能です")
            self.assertEqual(payload["segments"][0]["normalized_text"], "今回はClaude Codeの機能です")

    def test_duplicate_directory_gets_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first, first_name = runner.create_output_directory(temp_dir, "/input/example.wav", "20260723_163025")
            second, second_name = runner.create_output_directory(temp_dir, "/input/example.wav", "20260723_163025")
            self.assertEqual(first_name, "20260723_163025")
            self.assertEqual(second_name, "20260723_163025_02")
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
