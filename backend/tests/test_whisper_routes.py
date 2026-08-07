import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from routes import whisper as routes
except ModuleNotFoundError as exc:
    if exc.name not in {"fastapi", "pydantic"}:
        raise
    routes = None


class FakeRunningProcess:
    pid = 24680

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


@unittest.skipUnless(routes is not None, "FastAPI依存関係がないためルーターテストをスキップ")
class WhisperRoutesTest(unittest.TestCase):
    def setUp(self):
        routes._jobs.clear()

    def tearDown(self):
        routes._jobs.clear()

    def test_status_is_running_while_worker_is_alive(self):
        process = FakeRunningProcess()
        with routes._jobs_lock:
            routes._jobs["job"] = {
                "status": "error",
                "log": "",
                "result": None,
                "process": process,
                "worker_pid": process.pid,
                "process_returncode": None,
                "returncode": None,
                "cancel_requested": False,
                "current_position": "Worker実行中",
                "peak_rss_mb": 1368.9,
                "segment": 1,
                "total_segments": 6,
                "last_heartbeat_at": 0,
                "updated_at": 0,
            }

        payload = routes.status("job")

        self.assertEqual(payload["status"], "running")
        self.assertTrue(payload["worker_alive"])
        self.assertIsNone(payload["returncode"])
        self.assertEqual(payload["worker_pid"], process.pid)

    def test_terminal_state_cannot_return_to_running(self):
        with routes._jobs_lock:
            routes._jobs["job"] = {"status": "error", "log": "", "updated_at": 0, "last_heartbeat_at": 0}

        self.assertFalse(routes._set_job_status("job", "running"))
        self.assertEqual(routes._jobs["job"]["status"], "error")

    def test_worker_exception_does_not_make_running_worker_error(self):
        process = FakeRunningProcess()
        with routes._jobs_lock:
            routes._jobs["job"] = {
                "status": "running",
                "log": "",
                "result": None,
                "process": process,
                "worker_pid": process.pid,
                "process_returncode": None,
                "returncode": None,
                "cancel_requested": False,
                "current_position": "Worker実行中",
                "updated_at": 0,
                "last_heartbeat_at": 0,
            }

        with patch.object(routes, "_wait_for_worker"), patch.object(routes, "_worker_returncode", return_value=None):
            routes._run_job("job", lambda _job_id: (_ for _ in ()).throw(RuntimeError("一時的な監視失敗")))

        self.assertNotEqual(routes._jobs["job"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
