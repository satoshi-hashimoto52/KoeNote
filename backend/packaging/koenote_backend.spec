# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec（KoeNote Backend / onedir）。

開発用 .venv やリポジトリを参照しない自己完結の実行形式を作る。
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

BACKEND = Path(SPECPATH).resolve().parents[0]  # backend/

datas = [
    (str(BACKEND / "main.py"), "."),
    (str(BACKEND / "routes"), "routes"),
    (str(BACKEND / "services"), "services"),
    (str(BACKEND / "config"), "config"),
]
binaries = []
hiddenimports = []

# 動的 import が多いパッケージはまとめて回収する。
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface_hub"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# uvicorn / websockets のプロトコル実装は文字列で解決されるため明示する。
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("websockets")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.uvloop",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "routes.whisper",
    "routes.session",
    "services.live_session",
    "services.live_transcriber",
    "services.live_registry",
    "services.pcm_stream",
    "services.wav_recorder",
    "services.session_store",
    "services.word_commit",
    "services.transcriber",
    "services.exporter",
    "services.file_utils",
]

a = Analysis(
    [str(BACKEND / "packaging" / "koenote_backend.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    # 開発専用・巨大で不要なものは除外する。
    excludes=["tkinter", "pytest", "unittest", "pip", "setuptools", "torch", "whisper"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="koenote-backend",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="koenote-backend",
)
