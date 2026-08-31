# 04. ビルドと実行

> `package.json` の `scripts` と `README.md` に**実在するコマンドのみ**を記載します。
> 存在しないコマンド（lint / format / CI 等）は記載しません。

## install

`README.md` の「セットアップ」に記載された手順です。

```bash
# 1) Node 依存
npm install

# 2) Python 仮想環境
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

`README.md` の補足:

> `torch` / `openai-whisper` / `faster-whisper` は大型のため初回インストールに時間がかかります。
> 実推論を行わずサーバ起動・自動テストのみ確認する場合は、
> `pip install fastapi "uvicorn[standard]" python-multipart psutil` の軽量セットでも動作します。

## run

| コマンド | 定義 | 内容 |
|---|---|---|
| `npm run dev` | `concurrently -k -n VITE,ELECTRON ... "npm:dev:vite" "npm:dev:electron"` | Vite と Electron を同時起動 |
| `npm run dev:vite` | `vite` | Renderer の dev サーバのみ（port 5173 固定） |
| `npm run dev:electron` | `npm run build:electron && wait-on http://localhost:5173 && cross-env NODE_ENV=development electron .` | Electron のみ |
| `npm run preview` | `vite preview` | ビルド済み Renderer のプレビュー |

`scripts/start_app.command` からも開発モードで起動できます（先頭コメントより）。

Backend は Electron が子プロセスとして自動起動するため、単独起動のコマンドは
`package.json` に**ありません**。

## build

| コマンド | 定義 |
|---|---|
| `npm run build` | `npm run typecheck && npm run build:electron && npm run build:renderer` |
| `npm run build:electron` | `esbuild electron/main.ts electron/preload.ts --bundle --platform=node --format=cjs --target=node18 --outdir=electron/dist --out-extension:.js=.cjs --external:electron` |
| `npm run build:renderer` | `vite build` |
| `npm run typecheck` | `tsc --noEmit` |

## package（配布物の作成）

| コマンド | 定義 | 出力 |
|---|---|---|
| `npm run package:backend` | `rm -rf backend/packaging/build backend/packaging/dist && <python> -m PyInstaller --noconfirm --clean --distpath backend/packaging/dist --workpath backend/packaging/build backend/packaging/koenote_backend.spec` | `backend/packaging/dist/koenote-backend/` |
| `npm run package:mac` | `npm run package:backend && npm run build && electron-builder --mac --arm64` | `release/` |
| `npm run dist` | `npm run build && electron-builder` | `release/` |

`package:backend` / `package:mac` の Python は `./.venv/bin/python` があればそれを、
無ければ `python3` を使います（`package.json` 内の `node -e` による分岐）。

### 生成される成果物

`package.json` `build.mac.target` と `artifactName` より:

| 成果物 | パス |
|---|---|
| アプリ | `release/mac-arm64/KoeNote.app` |
| DMG | `release/KoeNote-0.1.0-arm64.dmg` |
| ZIP | `release/KoeNote-0.1.0-arm64.zip` |

出力先は `package.json` `build.directories.output` = `release` です。

## test

| コマンド | 定義 | 対象 |
|---|---|---|
| `npm run test:unit` | `vitest run --config vitest.config.ts` | `electron/**/*.test.ts`, `frontend/src/**/*.test.ts` |
| `npm run test:unit:watch` | `vitest --config vitest.config.ts --watch` | 同上（watch モード） |
| `npm run test:backend` | `cd backend && <python> -m unittest discover -s tests -p 'test_*.py'` | `backend/tests/test_*.py` |

`test:backend` の Python は `../.venv/bin/python` があればそれを、無ければ `python3` を使います。

### テストファイル一覧

| 対象 | ファイル数 | テスト件数 |
|---|---|---|
| TypeScript（Vitest） | 15 | **235** |
| Python（`unittest`） | 12 | **106** |

TypeScript:

`electron/backend-lifecycle.test.ts`, `electron/ipc/diagnostics.test.ts`,
`electron/ipc/openExternal.test.ts`, `electron/ipc/settingsMigration.test.ts`,
`frontend/src/components/deviceNotice.test.ts`,
`frontend/src/components/recordButton.test.ts`,
`frontend/src/components/sessionCleanup.test.ts`,
`frontend/src/components/settingsDraft.test.ts`,
`frontend/src/components/transcriptHeight.test.ts`,
`frontend/src/components/uiNotice.test.ts`,
`frontend/src/components/windowOpacity.test.ts`,
`frontend/src/features/transcription/acquireStream.test.ts`,
`frontend/src/features/transcription/inputDevice.test.ts`,
`frontend/src/features/transcription/watchdog.test.ts`,
`frontend/src/services/requestText.test.ts`

Python（`backend/tests/`）:

`test_attachments_removed.py`, `test_live_session.py`, `test_live_session_pcm.py`,
`test_live_ws_protocol.py`, `test_no_speech_filter.py`, `test_pcm_stream.py`,
`test_resolve_python_frozen.py`, `test_wav_recorder.py`, `test_whisper_routes.py`,
`test_whisper_runner.py`, `test_whisper_transcriber.py`, `test_word_commit.py`

## lint

**存在しません。**

- `package.json` に lint / format スクリプトはありません。
- ESLint / Prettier / EditorConfig の設定ファイルはリポジトリにありません。

型の静的検査は `npm run typecheck`（`tsc --noEmit`）が担っています。

## 長時間の実測（README 記載）

```bash
# Backend を起動しておく（npm run dev でもよい）
.venv/bin/python scripts/live_soak.py --minutes 120 --speed 90 \
  --output-folder /tmp/koenote_soak
```

補助スクリプト:

```bash
node scripts/audio_worklet_check.cjs
node scripts/pcm_pipeline_check.cjs
node scripts/responsive_check.cjs
```

> `responsive_check.cjs` の実行方法は `README.md` に記載がありません。
> 先頭コメントに「本番の Chromium(Electron) で built ページを各幅で読み込み、
> 横スクロール発生有無を測定する」とあります。
