import os
import tempfile
from pathlib import Path


def write_text_file(path: str | Path, text: str) -> None:
    """原子的にテキストを保存する。一時ファイルへ書き込み後 os.replace で置換する。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temporary_path = Path(temporary_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(str(text))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
