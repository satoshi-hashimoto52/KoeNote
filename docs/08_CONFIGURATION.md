# 08. 設定

> リポジトリ内に実在する設定ファイル・環境変数・定数のみを記載します。
> `.env` ファイル、`.env.example` はリポジトリに**存在しません**。

## 設定ファイル一覧

| ファイル | 用途 | Git 管理 |
|---|---|---|
| `package.json` | npm スクリプト・依存・electron-builder 設定 | ○ |
| `tsconfig.json` | TypeScript コンパイラ設定 | ○ |
| `vite.config.ts` | Renderer のビルド設定 | ○ |
| `vitest.config.ts` | テストランナー設定 | ○ |
| `backend/requirements.txt` | Python 依存 | ○ |
| `backend/config/transcription_terms.json` | 文字起こしの用語補正 | ○ |
| `backend/packaging/koenote_backend.spec` | PyInstaller 設定 | ○ |
| `build/entitlements.mac.plist` | macOS エンタイトルメント | ○ |
| `build/entitlements.mac.inherit.plist` | 子プロセス用エンタイトルメント | ○ |
| `~/Library/Application Support/KoeNote/koenote-settings.json` | ユーザー設定 | ✕（実行時生成） |

## 環境変数

すべて `KOENOTE_` 接頭辞です。

| 変数 | 既定値 | 用途 | 定義箇所 |
|---|---|---|---|
| `KOENOTE_PORT` | `8765` | Backend の待受ポート | `electron/backend.ts:9`, `electron/preload.ts:5-6` |
| `KOENOTE_PYTHON` | なし（`python3` へフォールバック） | 使用する Python の絶対パス | `electron/backend.ts:56`, `backend/services/transcriber.py:17` |
| `KOENOTE_FFMPEG_DIR` | なし | ffmpeg/ffprobe を含むディレクトリ | `electron/backend.ts:77-78`, `backend/services/transcriber.py:18` |
| `KOENOTE_MODEL_PATH` | なし | モデルファイルの絶対パス | `backend/services/transcriber.py:15` |
| `KOENOTE_MODEL_NAME` | `DEFAULT_MODEL_NAME` | モデル名 | `backend/services/transcriber.py:16` |

その他の環境変数:

| 変数 | 用途 | 定義箇所 |
|---|---|---|
| `NODE_ENV` | `development` で Vite dev サーバを読み込む | `package.json` `dev:electron`, `electron/main.ts` |
| `CHECK_SECONDS` | 検証スクリプトの実行秒数 | `scripts/pcm_pipeline_check.cjs` |

### 解決の優先順位

**Python**（`electron/backend.ts` `resolvePython`）

1. `<projectRoot>/.venv/bin/python`
2. `<backendDir>/.venv/bin/python`
3. `process.env.KOENOTE_PYTHON`
4. `python3`

**ffmpeg ディレクトリ**（`backend/services/transcriber.py` `resolve_ffmpeg_dir` の docstring）

1. 環境変数 `KOENOTE_FFMPEG_DIR`
2. 同梱 `resources/ffmpeg/bin`
3. `None`（= システム PATH）

`electron/backend.ts` の `FFMPEG_CANDIDATE_DIRS` は
`/opt/homebrew/bin` と `/usr/local/bin` です。

## ユーザー設定（koenote-settings.json）

| キー | 型 | 既定値 | 備考 |
|---|---|---|---|
| `gptUrl` | string | `''` | マイGPT の URL |
| `saveFolder` | string | `''` | セッション保存先 |
| `deviceId` | string | `''` | 入力デバイス ID |
| `deviceLabel` | string | `''` | 選択時のデバイス名。**0016 で追加** |
| `model` | string | `'small'` | `frontend/src/App.tsx` |
| `delayMode` | string | `'balanced'` | `frontend/src/App.tsx` |
| `requestTemplate` | string | `''` | 空なら組み込みテンプレートを使用 |
| `transcriptHeight` | number \| null | `null` | 未設定時は 320 を使う |
| `windowOpacity` | number | `1.00` | ウィンドウの不透明度。**0018 で追加** |

書き込みは `settings:set` IPC → 一時ファイル → `rename` のアトミック更新です。
**設定ファイルを書くのは Electron main だけ**で、Renderer は IPC 経由でしか触れません。

### 入力デバイス（deviceId / deviceLabel）

Chromium は `MediaDeviceInfo.deviceId` を **origin ごとに異なる値へソルト**します。
開発版（`http://localhost:5173`）で保存した ID はパッケージ版（`file://`）には存在しません。
そのため保存値は必ず現在のデバイス一覧と照合してから使います（Issue 0016）。

| 順 | 条件 | 使用する制約 |
|---|---|---|
| 1 | `audioinput` が 0 件 | エラー |
| 2 | 保存 ID が未設定 / `"default"` | 指定なし（OS 既定入力） |
| 3 | 保存 ID が一覧に存在 | その ID を `exact` 指定 |
| 4 | ID は無効だがラベル完全一致が 1 件だけ | 現 origin の ID を `exact` 指定 |
| 5 | ラベル一致なし／複数 | 指定なし（OS 既定入力）＋通知 |

- **フォールバックしただけでは `deviceId` / `deviceLabel` を恒久上書きしません。**
  USB 機器が一時的に外れているだけの場合に、内蔵マイクを恒久設定にしないためです。
- **ユーザーが設定画面で保存したときだけ**、新しい `deviceId` と `deviceLabel` を永続化します。
- `groupId` も origin 依存の可能性があるため、安定識別子として使っていません。

定義: `frontend/src/features/transcription/inputDevice.ts`

### ウィンドウの不透明度（windowOpacity）

| 項目 | 値 |
|---|---|
| 範囲 | **0.70〜1.00** |
| 初期値 | **1.00**（未設定・壊れている場合も 1.00） |
| UI の刻み | **5%**（設定モーダルの range スライダー、70〜100%） |
| 表示 | 「ウィンドウの不透明度　85%」と 1 行。補足は「100% = 透過なし」 |
| 適用方法 | `BrowserWindow.setOpacity()`。CSS の `opacity` は使わない |

- 不正な値（`NaN` / `Infinity` / 文字列 / number 以外）は既定 1.00 として扱います。
- 範囲外は clamp します（`0.69` → `0.70`、`1.01` → `1.00`）。
- スライダー操作中は保存せずにライブプレビューし、**キャンセル / Escape / 背景クリック /
  保存失敗**では開いた時点の値へ戻します。
- 起動時は main が `BrowserWindow` 生成直後・`show()` 前に適用します
  （100% で一瞬映るちらつきを防ぐため）。

定義: `frontend/src/components/windowOpacity.ts`, `electron/ipc/handlers.ts`, `electron/main.ts`

### 旧 BridgeLog からの移行

本アプリは以前 **BridgeLog** という名称でした。

| 項目 | 内容 |
|---|---|
| 移行元 | `~/Library/Application Support/BridgeLog/bridgelog-settings.json` |
| 移行先 | `~/Library/Application Support/KoeNote/koenote-settings.json` |
| 条件 | KoeNote 側の設定が**空**のとき（初回起動）のみ |
| 対象キー | `gptUrl` / `saveFolder` / `deviceId` / `deviceLabel` / `model` / `delayMode` / `requestTemplate` / `transcriptHeight` / `windowOpacity`（`MIGRATED_KEYS`） |
| 旧設定の扱い | **読み取りのみ**。変更も削除もしない |
| 旧設定に無いキー | 引き継がない（`deviceLabel` / `windowOpacity` は既定値になる） |
| 失敗時 | 現在の設定のまま続行 |
| 実行タイミング | `createWindow` より前（移行された `windowOpacity` を初回表示から反映するため） |

定義: `electron/ipc/settingsMigration.ts`, `electron/ipc/handlers.ts`, `electron/main.ts`

## Backend 起動引数

`electron/backend.ts` が uvicorn へ渡す固定引数です。

| 引数 | 値 | コード内コメント |
|---|---|---|
| `--host` | `BACKEND_HOST` | — |
| `--port` | `BACKEND_PORT`（既定 8765） | — |
| `--ws-max-size` | `1048576` | PCM フレームは 4KB 程度。既定の 16MiB より遥かに小さい上限にする |
| `--ws-max-queue` | `256` | — |
| `--ws-ping-interval` | `20` | 推論は別タスクへ分離したので PONG は常に読まれる |
| `--ws-ping-timeout` | `60` | 同上（余裕を持たせる） |
| `--ws-per-message-deflate` | `false` | PCM は圧縮が効かず CPU を食うだけ |
| `--timeout-keep-alive` | `30` | — |

## Whisper / 文字起こしの設定値

| 定数 | 値 | 定義箇所 |
|---|---|---|
| `SAMPLE_RATE` | `16000` | `backend/services/pcm_stream.py:12` |
| `BYTES_PER_SAMPLE` | `2` | `backend/services/pcm_stream.py:13` |
| `SUPPORTED_MODELS` | `{tiny, base, small, medium}` | `backend/services/live_transcriber.py:16` |
| `DEFAULT_MODEL` | `small` | `backend/services/live_transcriber.py:17` |
| `BUFFER_CAPACITY_SECONDS` | `180.0` | `backend/services/live_session.py:47` |
| `MIN_FLUSH_TAIL_SECONDS` | `0.5` | `backend/services/live_session.py:49` |
| `MAX_DEGRADE_LOG_LINES` | `200` | `backend/services/live_session.py:51` |
| `LAG_CATCHUP_THRESHOLD_SECONDS` | `30.0` | `backend/services/live_session.py:53` |
| `JOB_RETENTION_SECONDS` | `6 * 60 * 60` | `backend/routes/whisper.py` |

### 遅延プリセット

| `delay_mode` | `chunk_seconds` | `overlap_seconds` | UI ラベル |
|---|---|---|---|
| `low_latency` | 8.0 | 2.0 | 低遅延 |
| `balanced` | 10.0 | 2.0 | 標準 |
| `accuracy` | 12.0 | 3.0 | 精度優先 |

Backend 側は `backend/services/live_session.py:40`、
Renderer 側は `frontend/src/features/transcription/liveTypes.ts` の `LIVE_PRESETS` です。

## 用語補正（transcription_terms.json）

| キー | 型 | 内容 |
|---|---|---|
| `initial_prompt` | string | Whisper へ渡す初期プロンプト（ソフトウェア開発用語を列挙） |
| `replacements` | object | 置換辞書。**11 件** |
| `remove_fillers` | — | フィラー除去の設定 |

## UI の設定値

| 定数 | 値 | 定義箇所 |
|---|---|---|
| `DEFAULT_TRANSCRIPT_HEIGHT` | `320` | `frontend/src/components/transcriptHeight.ts:14` |
| `MIN_TRANSCRIPT_HEIGHT` | `180` | `:15` |
| `MAX_TRANSCRIPT_HEIGHT` | `1200` | `:16` |
| `VIEWPORT_RATIO` | `0.7` | `:18` |
| `TRANSCRIPT_HEIGHT_KEY` | `'transcriptHeight'` | `:13` |
| `MIN_WINDOW_OPACITY` | `0.7` | `frontend/src/components/windowOpacity.ts` |
| `MAX_WINDOW_OPACITY` | `1` | 同上 |
| `DEFAULT_WINDOW_OPACITY` | `1` | 同上 |
| `WINDOW_OPACITY_STEP` | `0.05` | 同上 |
| `WINDOW_OPACITY_KEY` | `'windowOpacity'` | 同上 |
| `FALLBACK_AUTO_DISMISS_MS` | `8000` | `frontend/src/components/uiNotice.ts` |

### 配色（0018）

入力欄とボタンの見え方は `:root` の CSS 変数で一元管理しています。
同じ用途へ個別に色を直書きしません。

| 変数 | 値 | 用途 |
|---|---|---|
| `--field-bg` | `#202024` | 入力欄・文字起こし本文の背景 |
| `--field-bg-hover` | `#26262b` | 入力欄の hover |
| `--field-border` | `rgba(255,255,255,0.14)` | 通常枠線 |
| `--field-border-hover` | `rgba(255,255,255,0.24)` | hover 枠線 |
| `--field-placeholder` | `#8b8b95` | placeholder |
| `--focus-ring` | `rgba(124,58,237,0.22)` | `:focus-visible` の外周リング |
| `--btn-neutral-bg` / `--btn-neutral-bg-hover` | `#26262b` / `#303036` | 中立ボタン |
| `--btn-neutral-border` / `--btn-neutral-border-hover` | `rgba(255,255,255,0.18)` / `0.30` | 中立ボタンの枠線 |
| `--disabled-opacity` | `0.62` | 無効時 |

`:focus` では `outline: none`、`:focus-visible` のときだけ枠線＋リングを出します
（マウスクリック後に枠を残さないため）。

### ウィンドウ

`electron/main.ts` の `BrowserWindow` 設定です。

| 項目 | 値 |
|---|---|
| `width` / `height` | `320` / `530` |
| `minWidth` / `minHeight` | `320` / `480` |
| `backgroundColor` | `#09090B` |
| `titleBarStyle` | `hiddenInset` |
| `title` | `KoeNote` |
| `contextIsolation` | `true` |
| `nodeIntegration` | `false` |
| `sandbox` | `true` |
| `backgroundThrottling` | `false` |

## macOS パッケージ設定

`package.json` `build` セクションです。

| 項目 | 値 |
|---|---|
| `appId` | `com.hashimoto.koenote` |
| `productName` | `KoeNote` |
| `files` | `electron/dist/**/*`, `frontend/dist/**/*` |
| `extraResources` | `backend/packaging/dist/koenote-backend` → `koenote-backend` |
| `mac.category` | `public.app-category.productivity` |
| `mac.target` | `dmg`(arm64), `zip`(arm64) |
| `artifactName` | `${productName}-${version}-${arch}.${ext}` |
| `NSMicrophoneUsageDescription` | 録音して文字起こしを行うためにマイクを使用します。 |
| `directories.output` | `release` |
| `directories.buildResources` | `build` |

### エンタイトルメント

`build/entitlements.mac.plist`（`entitlementsInherit` も同内容）:

| キー | 値 |
|---|---|
| `com.apple.security.cs.allow-jit` | `true` |
| `com.apple.security.cs.allow-unsigned-executable-memory` | `true` |
| `com.apple.security.cs.disable-library-validation` | `true` |
| `com.apple.security.device.audio-input` | `true` |

## Content Security Policy

`frontend/index.html` の `<meta http-equiv="Content-Security-Policy">`:

```text
default-src 'self';
script-src 'self' blob:;
worker-src 'self' blob:;
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*;
media-src 'self' blob:;
```

## Feature Flag

**存在しません。** 機能フラグの仕組みはリポジトリ内で確認できません。

`config.debug` / `set_debug` は WebSocket セッション単位のデバッグ表示切り替えであり、
機能フラグではありません。
