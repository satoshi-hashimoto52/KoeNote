# CLAUDE.md — KoeNote

> Claude Code 向けのプロジェクト指示書。
> 記載内容はすべてリポジトリ内のファイルを根拠にしています。**推測は含みません。**
>
> **配置について**: Claude Code はリポジトリ**ルート**の `CLAUDE.md` を自動読み込みします。
> 本ファイルは `docs/` に置かれているため自動読み込みされません。
> 自動読み込みさせたい場合はルートへ配置してください。

## プロジェクト概要

| 項目 | 値 |
|---|---|
| 名称 | KoeNote |
| バージョン | `0.1.0` |
| 説明 | 会議・セミナーの長時間文字起こしを行い、マイGPTへ渡す準備を整えるデスクトップアプリ |
| bundle identifier | `com.hashimoto.koenote` |
| 対象 | macOS / arm64 |
| 構成 | Electron main + React Renderer + FastAPI Backend |
| Backend ポート | `8765`（既定。`KOENOTE_PORT` で変更可） |
| 音声認識 | ローカル Whisper のみ。**OpenAI API は使用しない** |

## ディレクトリ構成

```text
KoeNote/
├── backend/          FastAPI（routes / services / tests / packaging / config）
├── electron/         Electron main・preload・IPC・Backend ライフサイクル
├── frontend/         React Renderer（Vite ルート）
├── scripts/          検証用スクリプト
├── docs/             設計・受け入れ試験・Issue
└── build/            electron-builder の buildResources（entitlements）
```

| パス | 役割 |
|---|---|
| `backend/routes/` | `whisper.py`（File Trans + `/ws/live`）、`session.py`（セッション管理） |
| `backend/services/` | `live_session.py`, `word_commit.py`, `wav_recorder.py`, `transcriber.py` ほか |
| `electron/ipc/` | `handlers.ts`（`ipcMain.handle` 13 件）、`openExternal.ts`, `diagnostics.ts`, `settingsMigration.ts` |
| `frontend/src/features/transcription/` | `useLiveTranscription.ts`, `pcmCapture.ts`, `watchdog.ts`, `liveTypes.ts`, `inputDevice.ts` |
| `frontend/src/components/` | React コンポーネントと UI 純関数（`deviceNotice` / `sessionCleanup` / `uiNotice` / `windowOpacity` / `InfoTip` ほか） |

## ビルド方法

```bash
npm run build            # typecheck → Electron ビルド → Renderer ビルド
npm run package:backend  # Backend を PyInstaller で単体実行形式へ
npm run package:mac      # .app / .dmg / .zip を release/ へ出力
```

成果物: `release/mac-arm64/KoeNote.app`, `release/KoeNote-0.1.0-arm64.dmg`,
`release/KoeNote-0.1.0-arm64.zip`

## テスト方法

```bash
npm run typecheck     # tsc --noEmit
npm run test:unit     # Vitest（TS 15 ファイル / 235 件）
npm run test:backend  # unittest（Python 12 ファイル / 106 件）
```

**Lint コマンドは存在しません。** ESLint / Prettier の設定もありません。
静的検査は `npm run typecheck` のみです。

## コーディングルール

| ルール | 根拠 |
|---|---|
| コメントは**日本語**で、「なぜそうしたか」を書く | 既存コード全般 |
| Issue 番号を根拠として引用する（例: `（0013）`） | `backend/routes/whisper.py`, `electron/preload.ts` |
| TypeScript は `strict: true`。`noUnusedLocals` / `noUnusedParameters` も有効 | `tsconfig.json` |
| 状態は**判別可能ユニオン**で表す | `LiveStatus`（`liveTypes.ts`） |
| Python の非公開関数は先頭 `_` | `_now_iso`, `_clamp_float` ほか |
| UI ロジックは純関数へ切り出してテストする | `recordButton.ts`, `settingsDraft.ts`, `transcriptHeight.ts`, `watchdog.ts` |
| 壊れた入力は既定値へフォールバックし、呼び出し側を止めない | `readSettings` ほか |
| FastAPI のエラーは `HTTPException` + 日本語 `detail` | `backend/routes/*.py` |
| ブロッキング処理は `asyncio.to_thread` へ逃がす | `backend/routes/whisper.py` |

### テストの置き場所

| 対象 | 場所 | 命名 |
|---|---|---|
| TypeScript | 実装と同じディレクトリ | `<対象>.test.ts` |
| Python | `backend/tests/` | `test_<対象>.py` |

## 編集禁止箇所

### 絶対に触らない

| 対象 | 理由 |
|---|---|
| 生成済みセッションフォルダ（`session.json` / `transcript.txt` / `recording.wav` / `diagnostics.log`） | ユーザーの録音成果物 |
| `~/Library/Application Support/BridgeLog/bridgelog-settings.json` | 旧設定。読み取り専用（`settingsMigration.ts`） |
| `package-lock.json` の手編集 | `npm install` 経由でのみ更新 |

### 履歴として維持する（機械的な一括置換をしない）

- `docs/issues/0001`〜`0015`
- `docs/manual-acceptance-long-transcription.md`
- `docs/migration_analysis.md`

これらに残る `BridgeLog` 表記は**当時の記録として意図的に維持**しています。
現行製品名の説明は `README.md` と `docs/00`〜`10` で行います。

### Git 管理に入れない（`.gitignore` 済み）

`release/`, `*.app`, `*.dmg`, `*.zip`, `backend/packaging/dist/`,
`backend/packaging/build/`, `.venv/`, `node_modules/`, Whisper モデル、
録音・文字起こし・セッション生成物

## 推奨ワークフロー

1. 該当する `docs/issues/` があれば先に読む。
2. 挙動を変える修正は、**先に失敗するテストを書く**。
3. 最小の修正を入れる。
4. `npm run typecheck` → `npm run test:unit` → `npm run test:backend` を全通過させる。
5. `git diff --check` と `git status --short` を確認する。
6. 独立したコミットにする。

### コミットメッセージ

既存 25 コミットの実績:

- 件名は**英語・小文字始まり・命令形**。接頭辞は `fix:` / `feat:` / `docs:` / `refactor:` / `build:`。
- 本文は**日本語**で「何を・なぜ」を書く。
- 例: `fix: prevent transcription watchdog false positives`

## 重要な設計思想

コード内のコメントから読み取れる、このプロジェクト固有の判断です。

| 思想 | 具体 |
|---|---|
| **録音は文字起こしと独立して残す** | 「推論が落ちても録音は残る」（`session_store.py`）。`CrashSafeWavWriter` は強制終了しても再生可能な WAV を残す |
| **異常記録を Backend に依存させない** | Backend 停止時にも書けるよう、Electron のローカル I/O で `diagnostics.log` を書く（Issue 0010） |
| **無関係な Backend を掴まない** | `/api/health` が `app: "KoeNote"` を名乗る場合のみ再利用する（`backend-lifecycle.ts`） |
| **黙って誤動作させず、明示的に失敗させる** | frozen 環境の `resolve_python()` は `RuntimeError` を投げる。`send_mode: "full"` はサーバが拒否する |
| **時間爆弾を作らない** | `--ws-max-size 1048576`。「25 分後の時間爆弾ではなく即座に失敗する」 |
| **状態量を有界に保つ** | 診断ログ 200 行、ジョブ保持 6 時間、リングバッファ 180 秒 |
| **受信をブロックしない** | WS 受信側は追記のみ。推論は別タスクへ |
| **Renderer に権限を与えない** | `contextIsolation` / `sandbox` 有効、公開 IPC は明示列挙のみ、外部 URL は許可ホストのみ |
| **UI ロジックを純関数へ出す** | 高さ計算・ボタン表示・停滞判定・設定移行・デバイス解決・通知分類・不透明度はすべてテスト可能な純関数 |
| **保存値を鵜呑みにしない** | `deviceId` は origin ごとにソルトされるため、必ず現在の一覧と照合する（0016） |
| **勝手に恒久設定へ書き戻さない** | フォールバックしただけでは保存しない。ユーザーが設定画面で保存したときだけ永続化する |
| **設定ファイルの書き手は main だけ** | Renderer と main が競合して書き換えない |

## よく使用するコマンド

```bash
# 開発起動（Vite + Electron + Backend 自動起動）
npm run dev

# 検証
npm run typecheck
npm run test:unit
npm run test:unit:watch
npm run test:backend

# ビルドと配布
npm run build
npm run package:backend
npm run package:mac

# Backend の健全性確認
curl -s http://127.0.0.1:8765/api/health

# 長時間の実測（Backend 起動中に実行）
.venv/bin/python scripts/live_soak.py --minutes 120 --speed 90 \
  --output-folder /tmp/koenote_soak

# 補助スクリプト
node scripts/audio_worklet_check.cjs
node scripts/pcm_pipeline_check.cjs
node scripts/responsive_check.cjs
```

## 未着手の Issue（着手前に本文を読むこと）

| Issue | 状態 |
|---|---|
| 0001 segment 境界でのテキスト欠落・重複 | 対応中 |
| 0002 停止時に未送信の PCM が破棄される | 未対応 |
| 0003 AudioWorklet の書きかけフレーム未フラッシュ | 未対応 |
| 0004 `segments_path` が指すファイルが生成されない | 未調査 |
| 0005 入力デバイスの誤選択に気付けない | 未対応（改善提案） |
| 0014 `session.json` が done になった後も WAV 処理が続く | 未調査 |

## 関連ドキュメント

| ファイル | 内容 |
|---|---|
| [`00_PROJECT_OVERVIEW.md`](00_PROJECT_OVERVIEW.md) | 概要・技術・実行方法 |
| [`01_ARCHITECTURE.md`](01_ARCHITECTURE.md) | 構成・データフロー・通信 |
| [`02_DIRECTORY_STRUCTURE.md`](02_DIRECTORY_STRUCTURE.md) | ディレクトリと各ファイルの役割 |
| [`03_TECH_STACK.md`](03_TECH_STACK.md) | ライブラリ一覧とバージョン |
| [`04_BUILD_AND_RUN.md`](04_BUILD_AND_RUN.md) | 実在するコマンド一覧 |
| [`05_CODING_CONVENTIONS.md`](05_CODING_CONVENTIONS.md) | 命名・エラー処理・型 |
| [`06_API_REFERENCE.md`](06_API_REFERENCE.md) | HTTP / WebSocket API |
| [`07_DATABASE.md`](07_DATABASE.md) | DB なし。ファイル永続化の仕様 |
| [`08_CONFIGURATION.md`](08_CONFIGURATION.md) | 設定・環境変数・既定値 |
| [`09_AI_DEVELOPMENT_GUIDE.md`](09_AI_DEVELOPMENT_GUIDE.md) | AI 向け作業ガイド |
| [`10_KNOWN_LIMITATIONS.md`](10_KNOWN_LIMITATIONS.md) | 制約と未解決事項 |
