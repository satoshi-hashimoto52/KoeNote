"""`transcript_segments.json` の逐次保存（0019 / 旧 #0004）。

`session.json` は以前から `"segments_path": "transcript_segments.json"` を
宣言していたが、このファイルを書き出すコードが存在しなかった。
0019 の障害解析と救済ではタイムスタンプ付きセグメントが決定的に有効だったため、
宣言どおり実際に書き出す方へ統一する。

方式:
    確定のたびに JSON Lines（`transcript_segments.jsonl`）へ 1 行追記し、
    セッション確定時に配列形式の `transcript_segments.json` へまとめ直す。

    - 追記は O(1)。長時間録音でもメモリを圧迫しない（全件を保持しない）
    - 途中でプロセスが落ちても、確定済みぶんは .jsonl に残る
    - `finalize()` を呼べていない .jsonl は `rebuild_from_jsonl()` で復旧できる

保存するのは**確定した（committed）セグメントだけ**。
partial は表示専用で、確定前に内容が変わるため保存しない。
"""
import json
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

from .file_utils import write_text_file

SEGMENTS_FILENAME = "transcript_segments.json"
SEGMENTS_JSONL_FILENAME = "transcript_segments.jsonl"

# 何行ごとに fsync するか。強制終了時の欠損はここまでに収まる。
DEFAULT_SYNC_INTERVAL_LINES = 20


def _round(value, digits: int = 3):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_record(
    start: float,
    end: float,
    text: str,
    *,
    state: str = "committed",
    avg_logprob=None,
    no_speech_prob=None,
    compression_ratio=None,
) -> dict:
    """1 セグメント分のレコード。時刻はセッション基準の絶対経過秒。"""
    record = {
        "start": _round(start),
        "end": _round(end),
        "text": str(text or ""),
        "state": state,
    }
    # 認識品質指標は取得できたときだけ入れる（無い値を 0 で埋めない）。
    for key, value in (
        ("avg_logprob", avg_logprob),
        ("no_speech_prob", no_speech_prob),
        ("compression_ratio", compression_ratio),
    ):
        rounded = _round(value, 4)
        if rounded is not None:
            record[key] = rounded
    return record


class SegmentsWriter:
    """確定セグメントを JSON Lines で逐次保存し、最後に JSON へまとめる。

    スレッドセーフ。`append` は推論スレッドから、`finalize` は停止処理から呼ばれる。
    """

    def __init__(
        self,
        session_dir,
        *,
        sync_interval_lines: int = DEFAULT_SYNC_INTERVAL_LINES,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.jsonl_path = self.session_dir / SEGMENTS_JSONL_FILENAME
        self.json_path = self.session_dir / SEGMENTS_FILENAME
        self.sync_interval_lines = max(int(sync_interval_lines), 1)
        self._lock = threading.Lock()
        self._count = 0
        self._since_sync = 0
        self._closed = False
        self.session_dir.mkdir(parents=True, exist_ok=True)
        # 再接続で同じセッションへ戻ることがあるので追記で開く。
        self._fh = open(self.jsonl_path, "a", encoding="utf-8")

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def append(self, record: dict) -> None:
        """1 件追記する。失敗しても録音・文字起こしは止めない。"""
        if self._closed:
            return
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            if self._closed:
                return
            try:
                self._fh.write(line + "\n")
                self._count += 1
                self._since_sync += 1
                if self._since_sync >= self.sync_interval_lines:
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                    self._since_sync = 0
            except OSError:
                # セグメント保存は補助情報。ここで例外を投げて録音を落とさない。
                pass

    def extend(self, records: Iterable[dict]) -> None:
        for record in records:
            self.append(record)

    def finalize(self) -> Optional[str]:
        """.jsonl を配列形式の JSON へまとめ、パスを返す。失敗時は None。"""
        with self._lock:
            if not self._closed:
                try:
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                except OSError:
                    pass
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._closed = True
        return rebuild_from_jsonl(self.session_dir)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._fh.close()
            except OSError:
                pass


def read_jsonl(session_dir) -> list[dict]:
    """.jsonl を読む。壊れた行は捨てて読める分だけ返す（部分復旧を優先する）。"""
    path = Path(session_dir) / SEGMENTS_JSONL_FILENAME
    if not path.is_file():
        return []
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                # 強制終了で最終行が途中まで、というのは起こりうる。読める分は残す。
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def rebuild_from_jsonl(session_dir) -> Optional[str]:
    """.jsonl から `transcript_segments.json` を作り直す。

    強制終了で `finalize()` を呼べなかったセッションの復旧にも使う。
    """
    directory = Path(session_dir)
    records = read_jsonl(directory)
    target = directory / SEGMENTS_FILENAME
    payload = {
        "app": "KoeNote",
        "segment_count": len(records),
        "segments": records,
    }
    try:
        write_text_file(target, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    except OSError:
        return None
    return str(target)
