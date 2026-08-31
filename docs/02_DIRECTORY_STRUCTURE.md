# 02. ディレクトリ構成

> Git 管理対象のファイル（**125 件**。本ドキュメント群と 0016〜0018 の追加分を含む）を対象にしています。
> `.gitignore` により除外されているもの（`node_modules/`, `.venv/`, `release/`,
> `backend/packaging/dist/`, `backend/packaging/build/`, `*.app`, `*.dmg`, `*.zip` 等）は含みません。

## ツリー

```text
KoeNote/
├── .gitignore
├── README.md
├── package.json
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
├── vitest.config.ts
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── config/
│   │   └── transcription_terms.json
│   ├── packaging/
│   │   ├── koenote_backend.py
│   │   └── koenote_backend.spec
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   └── whisper.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── exporter.py
│   │   ├── file_utils.py
│   │   ├── live_registry.py
│   │   ├── live_session.py
│   │   ├── live_transcriber.py
│   │   ├── pcm_stream.py
│   │   ├── runner.py
│   │   ├── session_store.py
│   │   ├── transcriber.py
│   │   ├── wav_recorder.py
│   │   └── word_commit.py
│   └── tests/
│       ├── __init__.py
│       └── test_*.py  (12 ファイル)
├── build/
│   ├── entitlements.mac.plist
│   └── entitlements.mac.inherit.plist
├── docs/
│   ├── 00_PROJECT_OVERVIEW.md 〜 10_KNOWN_LIMITATIONS.md
│   ├── CLAUDE.md
│   ├── DOCUMENTATION_REPORT.md
│   ├── issues/  (0001〜0016)
│   ├── manual-acceptance-long-transcription.md
│   └── migration_analysis.md
├── electron/
│   ├── main.ts
│   ├── preload.ts
│   ├── backend.ts
│   ├── backend-lifecycle.ts
│   ├── backend-lifecycle.test.ts
│   └── ipc/
│       ├── diagnostics.ts / .test.ts
│       ├── handlers.ts
│       ├── openExternal.ts / .test.ts
│       └── settingsMigration.ts / .test.ts
├── frontend/
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── styles.css
│       ├── components/          # UI 部品と UI 純関数（deviceNotice / sessionCleanup /
│       │                        #   uiNotice / windowOpacity / InfoTip ほか）
│       ├── features/transcription/  # 文字起こしロジック（inputDevice ほか）
│       ├── services/
│       └── types/
└── scripts/
    ├── audio_worklet_check.cjs
    ├── live_soak.py
    ├── measure_word_jitter.py
    ├── pcm_pipeline_check.cjs
    ├── responsive_check.cjs
    └── start_app.command
```

## 各フォルダの役割

| フォルダ | 役割 | 根拠 |
|---|---|---|
| `backend/` | FastAPI アプリ本体 | `backend/main.py` docstring |
| `backend/config/` | 文字起こしの用語補正データ | `transcription_terms.json` の内容 |
| `backend/packaging/` | PyInstaller のエントリと spec | `koenote_backend.py` docstring |
| `backend/routes/` | HTTP / WebSocket エンドポイント | `APIRouter` 定義 |
| `backend/services/` | ドメインロジック | 各モジュール |
| `backend/tests/` | `unittest` テスト | `package.json` `test:backend` |
| `build/` | electron-builder の buildResources | `package.json` `build.directories.buildResources` |
| `docs/` | 設計・受け入れ試験・Issue 文書 | 各ファイル |
| `electron/` | Electron main / preload / IPC | `package.json` `main` |
| `frontend/` | React Renderer | `vite.config.ts` `root` |
| `scripts/` | 検証用スクリプトと起動ショートカット | 各ファイル先頭コメント |

## 主要ファイルの役割

### ルート

| ファイル | 役割 |
|---|---|
| `package.json` | npm スクリプト、依存、electron-builder 設定 |
| `tsconfig.json` | `strict: true`、`noEmit: true`、対象は `frontend/src` と `electron` |
| `vite.config.ts` | Renderer のビルド設定。`root: frontend`、`base: './'`、port 5173 固定 |
| `vitest.config.ts` | `electron/**/*.test.ts` と `frontend/src/**/*.test.ts`、environment は `node` |

### backend/

| ファイル | 行数 | 役割 |
|---|---|---|
| `main.py` | 60 | FastAPI 生成、CORS、ルータ登録、`/api/health` |
| `routes/whisper.py` | 920 | File Trans のジョブ管理と `/ws/live` |
| `routes/session.py` | 105 | セッションフォルダ作成・確定・診断・WAV 修復 |
| `services/live_session.py` | 1071 | リアルタイム文字起こしのセッション状態と窓処理 |
| `services/word_commit.py` | 360 | word timestamp 単位の確定判定 |
| `services/transcriber.py` | 557 | File Trans の実行制御、ffmpeg/Python 解決 |
| `services/live_transcriber.py` | 347 | realtime の推論呼び出しとチャンク変換 |
| `services/wav_recorder.py` | 283 | クラッシュ耐性のある WAV ライタとレジストリ |
| `services/runner.py` | 280 | File Trans の別プロセスワーカー |
| `services/session_store.py` | 155 | セッションフォルダとメタデータ管理 |
| `services/pcm_stream.py` | — | PCM16LE mono ストリーム基盤・リサンプラ |
| `services/live_registry.py` | — | WS 切断後も LiveSession を保持するレジストリ |
| `services/exporter.py` | — | 出力ディレクトリ作成と成果物書き出し |
| `services/file_utils.py` | — | テキストファイル書き込み |
| `packaging/koenote_backend.py` | — | PyInstaller エントリ。`freeze_support()` を呼ぶ |
| `packaging/koenote_backend.spec` | — | onedir / `target_arch="arm64"` |

### electron/

| ファイル | 行数 | 役割 |
|---|---|---|
| `main.ts` | 189 | BrowserWindow、終了確認、スリープ抑止、異常通知 |
| `preload.ts` | — | `window.bridge` の公開（Node API は非公開） |
| `backend.ts` | 251 | Backend の起動・監視・再起動・ログ保持 |
| `backend-lifecycle.ts` | — | `classifyExit` / `createSingleFlight` / `isOwnBackendHealth` |
| `ipc/handlers.ts` | — | `ipcMain.handle` 12 件の登録 |
| `ipc/diagnostics.ts` | — | Backend 非依存の diagnostics.log 追記 |
| `ipc/openExternal.ts` | — | 許可ホスト判定と Chrome 起動 |
| `ipc/settingsMigration.ts` | — | 旧 BridgeLog 設定の移行計画（純関数） |

`ipc/handlers.ts` は `ipcMain.handle` を 13 件登録します（`window:setOpacity` を含む）。
`main.ts` は `ipcMain.on` を 2 件（`app:recordingState` / `app:anomaly`）登録します。

### frontend/src/

| ファイル | 行数 | 役割 |
|---|---|---|
| `App.tsx` | 652 | 画面全体の状態と組み立て |
| `styles.css` | 700 | スタイル（レスポンシブ含む） |
| `features/transcription/useLiveTranscription.ts` | 806 | WS 接続・送信・ウォッチドッグ |
| `features/transcription/pcmCapture.ts` | 196 | AudioWorklet による PCM キャプチャ |
| `features/transcription/liveTypes.ts` | — | `LiveStatus` / `LiveErrorReason` 等の型 |
| `features/transcription/watchdog.ts` | — | 停滞判定の純関数 |
| `features/transcription/alertTone.ts` | — | 異常時の警告音 |
| `components/SettingsModal.tsx` | 233 | 設定モーダル |
| `components/TranscriptView.tsx` | 184 | 文字起こし表示と高さ調整ハンドル |
| `components/GearIcon.tsx` | — | 設定アイコン（インライン SVG） |
| `components/transcriptHeight.ts` | — | 高さ計算の純関数 |
| `components/recordButton.ts` | — | 録音ボタンの表示解決（純関数） |
| `components/settingsDraft.ts` | — | 設定ドラフトの検証・確定（純関数） |
| `components/deviceNotice.ts` | — | 通知の重複防止・設定モーダルの選択値（純関数、0016） |
| `components/sessionCleanup.ts` | — | 開始失敗セッションの確定（冪等、0016） |
| `components/uiNotice.ts` | — | 通知の分類と 8 秒自動消去（純関数、0016） |
| `components/windowOpacity.ts` | — | 不透明度の範囲・正規化（main と共用、0018） |
| `components/InfoTip.tsx` | — | 小さな説明マーク（0017） |
| `features/transcription/inputDevice.ts` | — | 入力デバイスの解決と取得（純関数中心、0016） |
| `services/api.ts` | — | Backend API 呼び出しと依頼文生成 |
| `types/bridge.ts` | — | `window.bridge` の型 |

### scripts/

| ファイル | 役割（先頭コメントより） |
|---|---|
| `audio_worklet_check.cjs` | AudioWorklet を blob: URL から読む条件を本番と同じ形で確認する使い捨てスクリプト |
| `pcm_pipeline_check.cjs` | レンダラ→Backend のリアルタイム経路を本番と同じ条件で通しで検証する使い捨てスクリプト |
| `responsive_check.cjs` | 各幅で横スクロール発生有無を測定する使い捨てスクリプト |
| `live_soak.py` | 記載なし（shebang のみ） |
| `measure_word_jitter.py` | 記載なし（shebang のみ） |
| `start_app.command` | KoeNote を開発モードで起動する |
