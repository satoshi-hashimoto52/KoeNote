"""パッケージ版 Backend のエントリポイント。

PyInstaller で単体実行形式へ固める。開発版が
`python -m uvicorn main:app --host ... --port ...` で起動するのと同じ引数を受け取り、
uvicorn をプログラム的に起動する。

`main:app` の import 解決のため、frozen 環境では同梱した backend ディレクトリを
sys.path へ追加する。開発用の .venv やリポジトリには一切依存しない。
"""
import argparse
import multiprocessing
import os
import sys
from pathlib import Path


def _backend_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: 実行ファイルと同じ階層に backend の中身を置く。
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def _parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ws-max-size", type=int, default=1048576)
    parser.add_argument("--ws-max-queue", type=int, default=256)
    parser.add_argument("--ws-ping-interval", type=float, default=20.0)
    parser.add_argument("--ws-ping-timeout", type=float, default=60.0)
    parser.add_argument("--ws-per-message-deflate", default="false")
    parser.add_argument("--timeout-keep-alive", type=int, default=30)
    # 開発版と同じ呼び出し形（`-m uvicorn main:app`）で渡ってきても落ちないよう捨てる。
    known, _unknown = parser.parse_known_args(argv)
    return known


def main() -> int:
    root = _backend_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # services / routes を backend ルート基準で import できるようにする。
    os.chdir(str(root))

    args = _parse_args(sys.argv[1:])

    import uvicorn
    from main import app  # noqa: F401  backend/main.py

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ws_max_size=args.ws_max_size,
        ws_max_queue=args.ws_max_queue,
        ws_ping_interval=args.ws_ping_interval,
        ws_ping_timeout=args.ws_ping_timeout,
        ws_per_message_deflate=str(args.ws_per_message_deflate).lower() == "true",
        timeout_keep_alive=args.timeout_keep_alive,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    # PyInstaller で固めた実行形式では、multiprocessing の子プロセスが
    # 実行ファイルそのものを再実行する。freeze_support() を最初に呼ばないと
    # 子プロセスが uvicorn をもう一度起動し、ポート二重bindで失敗する。
    multiprocessing.freeze_support()
    sys.exit(main())
