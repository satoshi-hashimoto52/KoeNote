# 03. 技術スタック

> バージョンは `package.json`（宣言）と `package-lock.json`（解決済み）から取得しています。
> Python 側は `backend/requirements.txt` にバージョンピンが**ありません**。

## Node / TypeScript 依存

### dependencies

| ライブラリ | 宣言 | 解決済み | 用途 | 使用箇所 |
|---|---|---|---|---|
| `react` | `^18.3.1` | 18.3.1 | UI コンポーネント | `frontend/src/App.tsx`, `components/*.tsx` |
| `react-dom` | `^18.3.1` | 18.3.1 | React の DOM レンダリング | `frontend/src/main.tsx`（`createRoot`） |

### devDependencies

| ライブラリ | 宣言 | 解決済み | 用途 | 使用箇所 |
|---|---|---|---|---|
| `electron` | `^36.9.5` | 36.9.5 | デスクトップ実行環境 | `electron/main.ts`, `backend.ts`, `ipc/handlers.ts` |
| `electron-builder` | `^24.13.3` | 24.13.3 | `.app` / `.dmg` / `.zip` 生成 | `package.json` `scripts.dist`, `package:mac` |
| `esbuild` | `^0.23.0` | 0.23.1 | Electron main / preload のバンドル | `package.json` `scripts.build:electron` |
| `vite` | `^5.4.0` | 5.4.21 | Renderer のビルドと dev サーバ | `vite.config.ts` |
| `@vitejs/plugin-react` | `^4.3.1` | 4.7.0 | Vite の React 対応 | `vite.config.ts` |
| `vitest` | `^2.1.9` | 2.1.9 | TypeScript ユニットテスト | `vitest.config.ts`, `*.test.ts` 9 ファイル |
| `typescript` | `^5.6.3` | 5.9.3 | 型チェック | `tsconfig.json`, `scripts.typecheck` |
| `concurrently` | `^8.2.2` | 8.2.2 | Vite と Electron の同時起動 | `package.json` `scripts.dev` |
| `cross-env` | `^7.0.3` | 7.0.3 | `NODE_ENV` の設定 | `package.json` `scripts.dev:electron` |
| `wait-on` | `^7.2.0` | 7.2.0 | Vite 起動待ち | `package.json` `scripts.dev:electron` |
| `@types/node` | `^20.14.0` | 20.19.43 | Node 型定義 | `tsconfig.json` `types` |
| `@types/react` | `^18.3.3` | 18.3.31 | React 型定義 | `frontend/src` |
| `@types/react-dom` | `^18.3.0` | 18.3.7 | React DOM 型定義 | `frontend/src` |

## Python 依存

`backend/requirements.txt` の記載順です。バージョン指定は**ありません**。

### Core（コメント: 軽量。サーバ・ジョブ管理・テストに必要）

| ライブラリ | 用途 | 使用箇所 |
|---|---|---|
| `fastapi` | HTTP / WebSocket サーバ | `backend/main.py`, `routes/session.py`, `routes/whisper.py` |
| `uvicorn[standard]` | ASGI サーバ | `backend/packaging/koenote_backend.py` |
| `python-multipart` | `UploadFile` によるフォーム受信 | 直接 import なし（FastAPI が内部で使用） |
| `psutil` | プロセス情報の取得 | `backend/services/transcriber.py` |
| `numpy` | PCM / 音声配列処理 | `services/pcm_stream.py`, `services/live_transcriber.py`, 各テスト |

### 文字起こし（コメント: 大型。実推論に必要。ローカル処理のみで OpenAI API 課金なし）

| ライブラリ | 用途 | 使用箇所 |
|---|---|---|
| `openai-whisper` | File Trans の推論 | `backend/services/runner.py` |
| `torch` | `openai-whisper` の実行基盤 | `backend/services/runner.py` |
| `faster-whisper` | Realtime の推論 | `backend/services/live_transcriber.py` |

### requirements.txt に無いが使用されるもの

| ライブラリ | 用途 | 使用箇所 | 備考 |
|---|---|---|---|
| PyInstaller | Backend を単体実行形式へ固める | `package.json` `scripts.package:backend`, `backend/packaging/koenote_backend.spec` | `requirements.txt` に**記載なし** |
| `pydantic` | リクエストモデル | `backend/routes/session.py`, `routes/whisper.py` | FastAPI の依存として入る |
| `websockets` | `uvicorn[standard]` の WS 実装 | `koenote_backend.spec` の `collect_submodules` | `[standard]` extras 経由 |

## 外部バイナリ

| 名称 | 用途 | 探索方法 | 同梱 |
|---|---|---|---|
| `ffmpeg` | 音声変換 | 環境変数 `KOENOTE_FFMPEG_DIR` → 同梱 `resources/ffmpeg/bin` → システム PATH | **しない**（`README.md`） |
| `ffprobe` | 音声情報取得 | 同上 | **しない** |

`electron/backend.ts` の `FFMPEG_CANDIDATE_DIRS` は `/opt/homebrew/bin` と `/usr/local/bin` です。
`/api/health` が `ffmpeg_ok` を返し、解決状況を確認できます。

## モデル

| 項目 | 値 | 根拠 |
|---|---|---|
| 対応モデル | `tiny` / `base` / `small` / `medium` | `backend/services/live_transcriber.py:16` |
| 既定モデル | `small` | `backend/services/live_transcriber.py:17` |
| 取得元 | Hugging Face（`~/.cache/huggingface`） | `README.md` |
| 同梱 | **しない**（初回利用時に取得） | `README.md` |

## Linter / Formatter

ESLint、Prettier、EditorConfig の設定ファイルは**リポジトリに存在しません**。
`package.json` に lint / format スクリプトも**ありません**。

## CI / CD / コンテナ

| 項目 | 状態 |
|---|---|
| GitHub Actions（`.github/`） | **存在しない** |
| GitLab CI | **存在しない** |
| Dockerfile / docker-compose | **存在しない** |
| Makefile | **存在しない** |

CI/CD はこのプロジェクトでは確認できません。
