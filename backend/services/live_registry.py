"""WebSocket が切れても LiveSession を保持し、再接続で同じセッションに戻すレジストリ。

再接続で新しい LiveSession を作ってしまうと transcript.txt と recording.wav が
別ファイルに分かれ、絶対時刻の基準もリセットされる。同じ実体へ戻すことで
「切断されても続きから」を成立させる。
"""
import threading
import time
from typing import Optional

from .live_session import LiveSession

# この時間だけ再接続を待つ。過ぎたら回収して録音を確定させる。
SESSION_IDLE_TTL_SECONDS = 180.0


class _Entry:
    __slots__ = ("session", "last_seen_at", "attached")

    def __init__(self, session: LiveSession) -> None:
        self.session = session
        self.last_seen_at = time.time()
        self.attached = True


class LiveSessionRegistry:
    def __init__(self, idle_ttl_seconds: float = SESSION_IDLE_TTL_SECONDS) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self.idle_ttl_seconds = idle_ttl_seconds

    def register(self, session: LiveSession) -> None:
        with self._lock:
            self._reap_locked()
            self._entries[session.session_id] = _Entry(session)

    def acquire(self, session_id: str) -> Optional[LiveSession]:
        """再接続時に既存セッションを引き取る。既に別接続が使用中なら None。"""
        if not session_id:
            return None
        with self._lock:
            self._reap_locked()
            entry = self._entries.get(session_id)
            if entry is None or entry.attached:
                return None
            entry.attached = True
            entry.last_seen_at = time.time()
            return entry.session

    def detach(self, session_id: str) -> None:
        """接続が切れた。再接続を待てる状態にする。"""
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is not None:
                entry.attached = False
                entry.last_seen_at = time.time()

    def discard(self, session_id: str) -> None:
        """正常停止などで完全に終了した。"""
        with self._lock:
            self._entries.pop(session_id, None)

    def touch(self, session_id: str) -> None:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is not None:
                entry.last_seen_at = time.time()

    def reap(self) -> list[LiveSession]:
        with self._lock:
            return self._reap_locked()

    def _reap_locked(self) -> list[LiveSession]:
        now = time.time()
        expired = [
            session_id
            for session_id, entry in self._entries.items()
            if not entry.attached and now - entry.last_seen_at > self.idle_ttl_seconds
        ]
        reaped = []
        for session_id in expired:
            entry = self._entries.pop(session_id, None)
            if entry is not None:
                reaped.append(entry.session)
        return reaped

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


registry = LiveSessionRegistry()
