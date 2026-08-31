"""パッケージ版(PyInstaller)で resolve_python が安全に失敗することの確認。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.transcriber import PYTHON_ENV, resolve_python  # noqa: E402


class ResolvePythonFrozenTest(unittest.TestCase):
    def test_frozen_without_override_raises(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.dict(os.environ, {PYTHON_ENV: ""}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                resolve_python()
        self.assertIn(PYTHON_ENV, str(ctx.exception))

    def test_frozen_with_override_uses_it(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
             mock.patch.dict(os.environ, {PYTHON_ENV: sys.executable}, clear=False):
            self.assertEqual(resolve_python(), sys.executable)

    def test_not_frozen_uses_sys_executable(self):
        with mock.patch.dict(os.environ, {PYTHON_ENV: ""}, clear=False):
            self.assertEqual(resolve_python(), sys.executable)


if __name__ == "__main__":
    unittest.main()
