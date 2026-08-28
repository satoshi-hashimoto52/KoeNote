import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import session_store


class AttachmentsRemovalTest(unittest.TestCase):
    """0007: 資料機能の削除と、旧セッションとの互換。"""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_new_session_json_has_no_attachments_key(self):
        result = session_store.create_meeting_directory(
            output_base=str(self.base), title="テスト", gpt_url="https://chatgpt.com/x"
        )
        session = json.loads(Path(result["session_json_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("attachments", session)

    def test_new_session_does_not_create_attachments_json(self):
        result = session_store.create_meeting_directory(
            output_base=str(self.base), title="テスト", gpt_url=""
        )
        self.assertFalse((Path(result["session_dir"]) / "attachments.json").exists())
        self.assertNotIn("attachments_json_path", result)

    def test_create_accepts_no_attachments_argument(self):
        # 内部引数から削除済み。呼び出し側が渡さない形で動く。
        result = session_store.create_meeting_directory(
            output_base=str(self.base), title="引数なし", gpt_url=""
        )
        self.assertTrue(Path(result["session_json_path"]).is_file())

    def test_reading_legacy_session_with_attachments_does_not_fail(self):
        """既存セッションの attachments キーと attachments.json は残す・壊さない。"""
        result = session_store.create_meeting_directory(
            output_base=str(self.base), title="旧", gpt_url=""
        )
        meeting_dir = Path(result["session_dir"])
        # 旧形式を再現する
        legacy = json.loads((meeting_dir / "session.json").read_text(encoding="utf-8"))
        legacy["attachments"] = ["/tmp/a.pdf", "/tmp/b.txt"]
        (meeting_dir / "session.json").write_text(
            json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (meeting_dir / "attachments.json").write_text('{"attachments": []}\n', encoding="utf-8")

        loaded = session_store.read_session(str(meeting_dir))
        self.assertEqual(loaded["attachments"], ["/tmp/a.pdf", "/tmp/b.txt"])

        # finalize しても attachments を消さない・壊さない
        session_store.finalize_session(str(meeting_dir), "done", None)
        after = session_store.read_session(str(meeting_dir))
        self.assertEqual(after["attachments"], ["/tmp/a.pdf", "/tmp/b.txt"])
        self.assertEqual(after["status"], "done")
        self.assertTrue((meeting_dir / "attachments.json").is_file())

    def test_write_attachments_is_removed(self):
        self.assertFalse(hasattr(session_store, "write_attachments"))
        self.assertFalse(hasattr(session_store, "ATTACHMENTS_FILENAME"))
