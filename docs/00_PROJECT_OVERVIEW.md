# 00. プロジェクト概要

> 本書は KoeNote リポジトリ内に実在するファイルのみを根拠に作成しています。
> リポジトリから確認できない事項は「不明」または「このプロジェクトでは確認できない」と記載します。

## プロジェクト概要

| 項目 | 値 | 根拠 |
|---|---|---|
| 名称 | KoeNote | `package.json` `productName` |
| パッケージ名 | `koenote` | `package.json` `name` |
| バージョン | `0.1.0` | `package.json` `version` |
| 説明 | 会議・セミナーの長時間文字起こしを行い、マイGPTへ渡す準備を整えるデスクトップアプリ | `package.json` `description` |
| 作者 | hashimoto | `package.json` `author` |
| 公開設定 | `private: true` | `package.json` |
| bundle identifier | `com.hashimoto.koenote` | `package.json` `build.appId` |
| 対象プラットフォーム | macOS / arm64 | `package.json` `build.mac.target` |
| ライセンス | 不明（`LICENSE` ファイルおよび `package.json` に記載なし） | リポジトリ走査 |

## 解決する課題

`README.md` に記載された内容のみを転記します。

- 会議・セミナーの**長時間文字起こし**を行う。
- 事前登録したタイトル・マイGPT URL とともに、**マイGPT（ChatGPT の GPTs）へ渡す準備**を整える。
- 音声認識は**ローカルの Whisper** で行い、**OpenAI API は一切使用しない**（追加の従量課金なし）。

## 主な機能

コードから確認できる機能のみを記載します。

| 機能 | 実体 | 根拠 |
|---|---|---|
| リアルタイム文字起こし | WebSocket `/ws/live` で PCM16 を受信し逐次確定 | `backend/routes/whisper.py:671` |
| ファイル一括文字起こし（File Trans） | `/api/whisper/transcribe`, `/api/whisper/upload` | `backend/routes/whisper.py:292,314` |
| 会議セッション管理 | フォルダ作成・確定・診断ログ | `backend/routes/session.py` |
| 録音音声の WAV 保存 | 文字起こしとは別系統でクラッシュ耐性を持つ | `backend/services/wav_recorder.py` |
| WAV ヘッダ修復 | `/api/session/repair_audio` | `backend/routes/session.py:92` |
| word 単位の確定判定 | word timestamp 単位で確定境界を管理 | `backend/services/word_commit.py` |
| 異常検知（ウォッチドッグ） | 9 種の異常理由を列挙 | `frontend/src/features/transcription/liveTypes.ts` |
| マイGPT を Chrome で開く | 許可ホストのみ。失敗時は既定ブラウザ | `electron/ipc/openExternal.ts` |
| 設定移行（BridgeLog → KoeNote） | 初回起動時のみ既定キーを引き継ぐ | `electron/ipc/settingsMigration.ts` |
| 入力デバイスの再解決 | origin 変更で無効になった `deviceId` をラベルで引き当て直す | `frontend/src/features/transcription/inputDevice.ts` |
| 開始失敗セッションの確定 | マイク取得に失敗しても `status: recording` の空セッションを残さない | `frontend/src/components/sessionCleanup.ts` |
| ウィンドウの不透明度 | 70〜100% を設定モーダルから変更（`BrowserWindow.setOpacity`） | `frontend/src/components/windowOpacity.ts` |
| 用語補正 | `initial_prompt` と置換辞書 | `backend/config/transcription_terms.json` |

## 使用技術

| 層 | 技術 | バージョン | 根拠 |
|---|---|---|---|
| デスクトップ | Electron | `^36.9.5` | `package.json` devDependencies |
| UI | React / React DOM | `^18.3.1` | `package.json` dependencies |
| ビルド（Renderer） | Vite | `^5.4.0` | `package.json` devDependencies |
| ビルド（Electron） | esbuild | `^0.23.0` | `package.json` devDependencies |
| 言語 | TypeScript | `^5.6.3` | `package.json` devDependencies |
| テスト（TS） | Vitest | `^2.1.9` | `package.json` devDependencies |
| 配布 | electron-builder | `^24.13.3` | `package.json` devDependencies |
| Backend | FastAPI + uvicorn[standard] | バージョン指定なし | `backend/requirements.txt` |
| 音声認識（Realtime） | faster-whisper | バージョン指定なし | `backend/requirements.txt` |
| 音声認識（File Trans） | openai-whisper + torch | バージョン指定なし | `backend/requirements.txt` |
| テスト（Python） | `unittest`（標準ライブラリ） | — | `package.json` `test:backend` |
| 外部バイナリ | ffmpeg / ffprobe | 不明（同梱しない） | `README.md`, `backend/services/transcriber.py` |

> `backend/requirements.txt` にバージョンピンは**ありません**。

## ディレクトリ概要

| ディレクトリ | 役割 |
|---|---|
| `backend/` | FastAPI アプリ本体（ルート・サービス・テスト・パッケージング） |
| `electron/` | Electron main / preload / IPC |
| `frontend/` | React Renderer（Vite ルート） |
| `scripts/` | 検証用スクリプトと起動ショートカット |
| `docs/` | 設計・受け入れ試験・Issue 文書 |
| `build/` | electron-builder の buildResources（entitlements） |

詳細は [`02_DIRECTORY_STRUCTURE.md`](02_DIRECTORY_STRUCTURE.md) を参照してください。

## 実行方法

```bash
npm run dev
```

`package.json` `scripts.dev` は Vite と Electron を `concurrently` で同時起動します。
Backend は Electron が子プロセスとして自動起動します（`electron/backend.ts`）。

## ビルド方法

```bash
# 型チェック → Electron ビルド → Renderer ビルド
npm run build

# Backend を PyInstaller で単体実行形式へ固める
npm run package:backend

# .app / .dmg / .zip を release/ へ出力
npm run package:mac
```

## テスト方法

```bash
# TypeScript ユニットテスト
npm run test:unit

# Python ユニットテスト
npm run test:backend

# 型チェック
npm run typecheck
```

現在の件数（実測）:

| 対象 | ファイル数 | テスト件数 |
|---|---|---|
| Vitest（TypeScript） | 15 | **235** |
| Backend（Python `unittest`） | 12 | **106** |

Lint コマンドは `package.json` に**存在しません**。ESLint / Prettier の設定ファイルもリポジトリに存在しません。

全コマンドは [`04_BUILD_AND_RUN.md`](04_BUILD_AND_RUN.md) を参照してください。
