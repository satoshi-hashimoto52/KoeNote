# 09. AI コーディング支援ガイド

> 本書はリポジトリ内の事実のみを根拠にしています。
> 判断に迷う場合は推測せず、該当ファイルを読んで確認してください。

## プロジェクト概要

| 項目 | 値 |
|---|---|
| 名称 | KoeNote `0.1.0` |
| 目的 | 会議・セミナーの長時間文字起こしと、マイGPT へ渡す準備 |
| 構成 | Electron main + React Renderer + FastAPI Backend の 3 プロセス |
| 対象 | macOS / arm64 |
| 音声認識 | ローカル Whisper のみ（OpenAI API は使用しない） |
| Backend ポート | `8765`（既定） |

## ディレクトリ説明

| パス | 内容 |
|---|---|
| `backend/routes/` | FastAPI のエンドポイント |
| `backend/services/` | ドメインロジック |
| `backend/tests/` | `unittest` テスト |
| `backend/packaging/` | PyInstaller のエントリと spec |
| `electron/` | main / preload / Backend ライフサイクル |
| `electron/ipc/` | IPC ハンドラと純関数 |
| `frontend/src/components/` | React コンポーネントと UI 純関数 |
| `frontend/src/features/transcription/` | 文字起こしロジック |
| `frontend/src/services/` | Backend API 呼び出し |
| `scripts/` | 検証用スクリプト |
| `docs/` | 設計・受け入れ試験・Issue |

詳細は [`02_DIRECTORY_STRUCTURE.md`](02_DIRECTORY_STRUCTURE.md) を参照してください。

## 編集してよい場所

| 対象 | 補足 |
|---|---|
| `backend/routes/`, `backend/services/` | テストを伴うこと |
| `docs/00`〜`10`, `docs/CLAUDE.md`, `docs/DOCUMENTATION_REPORT.md` | 実装を変えたら追従させる |
| `backend/tests/` | ファイル名は `test_*.py` |
| `electron/`, `electron/ipc/` | `tsc --noEmit` が通ること |
| `frontend/src/` | 同上 |
| `*.test.ts`, `test_*.py` | テストは実装と同じ場所（TS）／`backend/tests/`（Python） |
| `docs/` の新規ファイル | 既存 Issue と受け入れ試験記録は下記参照 |
| `README.md` | 現行仕様を説明する箇所 |

## 編集禁止・注意箇所

### 絶対に触らないもの

| 対象 | 理由 |
|---|---|
| 生成済みのセッションフォルダ（`session.json` / `transcript.txt` / `recording.wav` / `diagnostics.log`） | ユーザーの録音成果物。コードから書き換える経路も作らない |
| `~/Library/Application Support/BridgeLog/bridgelog-settings.json` | 旧設定。**読み取り専用**（`electron/ipc/settingsMigration.ts`） |
| `package-lock.json` の手編集 | `npm install` 経由でのみ更新する |

### 履歴として維持するもの

| 対象 | 理由 |
|---|---|
| `docs/issues/0001`〜`0015` | 当時の調査記録。`BridgeLog` 表記も当時のまま維持する |
| `docs/manual-acceptance-long-transcription.md` | 受け入れ試験の記録 |
| `docs/migration_analysis.md` | 移植調査の記録 |

これらを**機械的に一括置換しないでください**。現行仕様の説明は README と本 docs 群で行います。

### Git 管理に入れてはいけないもの

`.gitignore` により除外済みです。追加しないでください。

- `release/`, `*.app`, `*.dmg`, `*.zip`
- `backend/packaging/dist/`, `backend/packaging/build/`
- `.venv/`, `backend/.venv/`, `node_modules/`
- Whisper モデル、録音・文字起こし・セッション生成物

## 実装ルール

コード内のコメントと既存実装から読み取れる方針です。

| ルール | 根拠 |
|---|---|
| Renderer から Node API を使わない | `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` |
| IPC は `electron/preload.ts` に明示列挙したものだけ公開する | 同ファイルのコメント「必要最小限の IPC だけを公開する」 |
| 外部 URL は許可ホストのみ開く | `ALLOWED_GPT_HOSTS = ['chatgpt.com', 'chat.openai.com']` |
| 外部コマンドは `execFile` に配列で渡す（`shell: true` を使わない） | `electron/ipc/openExternal.ts` のコメント |
| Backend の再利用は `/api/health` の `app` が `KoeNote` の場合のみ | `electron/backend-lifecycle.ts` |
| `send_mode` は `pcm16` を使う。`full` は使わない | サーバが拒否（O(T^2) のため） |
| WebSocket の受信側はブロックさせない | `backend/routes/whisper.py` のコメント「受信側は追記だけ」 |
| ブロッキング処理は `asyncio.to_thread` へ逃がす | 推論・WAV 解放の実装 |
| 録音は文字起こしと別系統で保存する | `session.json` のコメント「推論が落ちても録音は残る」 |
| 異常記録は Backend に依存させない | `electron/ipc/diagnostics.ts`（Issue 0010） |
| UI ロジックは純関数へ切り出してテストする | `recordButton.ts`, `settingsDraft.ts`, `transcriptHeight.ts`, `watchdog.ts`, `inputDevice.ts`, `uiNotice.ts`, `windowOpacity.ts`, `sessionCleanup.ts`, `deviceNotice.ts` |
| 保存済み `deviceId` は必ず現在の一覧と照合してから使う | origin ごとにソルトされる（0016） |
| 通知の分類は文字列一致ではなく `kind` で判定する | `frontend/src/components/uiNotice.ts` |
| 設定ファイルを書くのは Electron main だけ | Renderer は `settings:set` IPC 経由 |
| 不透明度は `BrowserWindow.setOpacity` を使う（CSS の `opacity` は使わない） | `electron/ipc/handlers.ts` |
| 入力欄・ボタンの色は `:root` の CSS 変数へ集約する | `--field-*` / `--btn-neutral-*`（0018） |

## コーディング規約

[`05_CODING_CONVENTIONS.md`](05_CODING_CONVENTIONS.md) を参照してください。要点のみ:

- コメントは**日本語**。「なぜそうしたか」を書く。
- Issue 番号を根拠として引用する（例: `（0013）`）。
- TypeScript は `strict: true`。状態は判別可能ユニオンで表す。
- Python の非公開関数は先頭 `_`。
- Linter は**存在しない**。`tsc --noEmit` が唯一の静的検査。

## テスト方法

```bash
npm run typecheck     # tsc --noEmit
npm run test:unit     # Vitest（electron/**, frontend/src/**）
npm run test:backend  # unittest（backend/tests/）
```

テストの置き場所:

| 対象 | 場所 | 命名 | 現在の件数 |
|---|---|---|---|
| TypeScript | 実装と同じディレクトリ | `<対象>.test.ts` | 15 ファイル / **235 件** |
| Python | `backend/tests/` | `test_<対象>.py` | 12 ファイル / **106 件** |

時間に依存するロジックは Vitest の fake timers を使い、実時間を待ちません
（例: `uiNotice.test.ts` の 8 秒自動消去）。

## コミット前確認事項

```bash
npm run typecheck
npm run test:unit
npm run test:backend
git diff --check
git status --short
```

確認項目:

- [ ] `npm run typecheck` がエラーなしで完了する
- [ ] `npm run test:unit` が全件通過する
- [ ] `npm run test:backend` が全件通過する
- [ ] `git diff --check` が空（行末空白・コンフリクトマーカーなし）
- [ ] `git status --short` に成果物（`release/`, `*.app`, `*.dmg`, `*.zip`, `dist/`）が含まれない
- [ ] 認証情報・APIキー・個人情報を含まない
- [ ] ユーザー固有の絶対パス（`/Users/<name>/...`）をソースと README に残していない
- [ ] 生成済みセッションデータを変更していない

### コミットメッセージ

既存 25 コミットの実績です。

| 接頭辞 | 件数 | 例 |
|---|---|---|
| `fix:` | 6 | `fix: stabilize resizable transcript panel` |
| `feat:` | 4 | `feat: support narrower window sizes` |
| `docs:` | 4 | `docs: finalize long transcription acceptance results` |
| `refactor:` | 1 | `refactor: remove attachments feature` |
| `build:` | 1 | `build: rename and package KoeNote for macOS arm64` |

- 件名は**英語・小文字始まり・命令形**。
- 本文は**日本語**で「何を・なぜ」を書く（`git log -1 --format=%b` で確認できます）。
- 直近のコミットには `_YYYYMMDD_HHMM` の日時サフィックスが付いています（hook 由来）。

## Pull Request 時の確認事項

> このリポジトリに GitHub Actions（`.github/`）は**存在しません**。
> CI による自動検証は行われないため、以下はすべて手動で確認してください。

- [ ] 上記「コミット前確認事項」をすべて満たしている
- [ ] 変更の根拠を説明できる（該当する Issue 番号があれば引用する）
- [ ] 挙動を変える修正には、**先に失敗するテスト**を追加している
- [ ] 既存の Issue 文書・受け入れ試験記録を機械的に書き換えていない
- [ ] 公開リポジトリであることを踏まえ、機密情報が含まれていない
- [ ] 依存を追加した場合、`package.json` または `backend/requirements.txt` に反映している

### PR テンプレート

**存在しません**（`.github/` がないため）。

## 未着手として明示されている項目

`docs/issues/` の記載に基づきます。着手前に該当 Issue を読んでください。

| Issue | 状態 |
|---|---|
| 0001 segment 境界でのテキスト欠落・重複 | 対応中 |
| 0002 停止時に未送信の PCM が破棄される | 未対応 |
| 0003 AudioWorklet の書きかけフレーム未フラッシュ | 未対応 |
| 0004 `segments_path` が指すファイルが生成されない | 未調査（仕様確認が必要） |
| 0005 入力デバイスの誤選択に気付けない | 未対応（改善提案） |
| 0014 `session.json` が done になった後も WAV 処理が続く | 未調査 |

詳細は [`10_KNOWN_LIMITATIONS.md`](10_KNOWN_LIMITATIONS.md) を参照してください。
