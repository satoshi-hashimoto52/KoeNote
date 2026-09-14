"""transcript_segments.json の逐次保存と復旧（0019 / 旧 #0004）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.segments_writer import (  # noqa: E402
    SEGMENTS_FILENAME,
    SEGMENTS_JSONL_FILENAME,
    SegmentsWriter,
    build_record,
    read_jsonl,
    rebuild_from_jsonl,
)


class BuildRecordTest(unittest.TestCase):
    def test_keeps_absolute_seconds_and_state(self):
        record = build_record(12.3456, 15.9, "こんにちは")
        self.assertEqual(record["start"], 12.346)
        self.assertEqual(record["end"], 15.9)
        self.assertEqual(record["text"], "こんにちは")
        self.assertEqual(record["state"], "committed")

    def test_omits_quality_metrics_when_unavailable(self):
        record = build_record(0.0, 1.0, "あ")
        self.assertNotIn("avg_logprob", record)
        self.assertNotIn("compression_ratio", record)

    def test_includes_quality_metrics_when_given(self):
        record = build_record(0.0, 1.0, "あ", avg_logprob=-0.31, no_speech_prob=0.12,
                              compression_ratio=1.45)
        self.assertEqual(record["avg_logprob"], -0.31)
        self.assertEqual(record["no_speech_prob"], 0.12)
        self.assertEqual(record["compression_ratio"], 1.45)


class SegmentsWriterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_appends_jsonl_then_finalizes_to_json(self):
        writer = SegmentsWriter(self.dir)
        writer.append(build_record(0.0, 1.0, "あ"))
        writer.append(build_record(1.0, 2.0, "い"))
        path = writer.finalize()

        self.assertEqual(path, str(self.dir / SEGMENTS_FILENAME))
        payload = json.loads((self.dir / SEGMENTS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["segment_count"], 2)
        self.assertEqual([s["text"] for s in payload["segments"]], ["あ", "い"])

    def test_does_not_hold_all_records_in_memory(self):
        """長時間録音でメモリを圧迫しないこと。件数だけを持つ。"""
        writer = SegmentsWriter(self.dir, sync_interval_lines=1000)
        for i in range(5000):
            writer.append(build_record(i * 1.0, i * 1.0 + 0.5, f"語{i}"))
        self.assertEqual(writer.count, 5000)
        # 保持しているのはカウンタとファイルハンドルだけ
        self.assertFalse(
            any(isinstance(v, list) and len(v) > 100 for v in vars(writer).values()),
            "レコードを配列で保持している",
        )
        writer.finalize()
        payload = json.loads((self.dir / SEGMENTS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["segment_count"], 5000)

    def test_recovers_from_a_truncated_last_line(self):
        """強制終了で最終行が途中まででも、読める分は復旧できること。"""
        writer = SegmentsWriter(self.dir)
        writer.append(build_record(0.0, 1.0, "あ"))
        writer.append(build_record(1.0, 2.0, "い"))
        writer.close()
        with open(self.dir / SEGMENTS_JSONL_FILENAME, "a", encoding="utf-8") as fh:
            fh.write('{"start": 2.0, "end": 3.0, "te')

        records = read_jsonl(self.dir)
        self.assertEqual([r["text"] for r in records], ["あ", "い"])
        rebuild_from_jsonl(self.dir)
        payload = json.loads((self.dir / SEGMENTS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["segment_count"], 2)

    def test_reopening_appends_instead_of_truncating(self):
        """再接続で同じセッションへ戻っても既存分を消さないこと。"""
        first = SegmentsWriter(self.dir)
        first.append(build_record(0.0, 1.0, "あ"))
        first.close()
        second = SegmentsWriter(self.dir)
        second.append(build_record(1.0, 2.0, "い"))
        second.finalize()
        payload = json.loads((self.dir / SEGMENTS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual([s["text"] for s in payload["segments"]], ["あ", "い"])

    def test_append_after_close_is_ignored(self):
        writer = SegmentsWriter(self.dir)
        writer.append(build_record(0.0, 1.0, "あ"))
        writer.finalize()
        writer.append(build_record(1.0, 2.0, "い"))
        payload = json.loads((self.dir / SEGMENTS_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["segment_count"], 1)

    def test_rebuild_on_missing_jsonl_makes_an_empty_file(self):
        path = rebuild_from_jsonl(self.dir)
        self.assertIsNotNone(path)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(payload["segment_count"], 0)


class SessionStoreDeclarationTest(unittest.TestCase):
    def test_session_json_declares_the_same_filename(self):
        """session.json の宣言と実ファイル名が一致していること（#0004 の再発防止）。"""
        from services import session_store

        self.assertEqual(session_store.SEGMENTS_FILENAME, SEGMENTS_FILENAME)


if __name__ == "__main__":
    unittest.main()
