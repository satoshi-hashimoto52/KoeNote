# BridgeLog

会議・セミナーの**長時間文字起こし**を行い、事前登録したタイトル・資料・マイGPT URL とともに、
文字起こし完了後に**マイGPT（ChatGPT の GPTs）へ渡す準備**を整える macOS 向け Electron デスクトップアプリ。

音声認識は **ローカルの Whisper**（File Trans: `openai-whisper` / Realtime: `faster-whisper`）で行い、
**OpenAI API は一切使用しません**（追加の従量課金なし）。

> Whisper 文字起こし機能は既存プロジェクト `my_launcher` から移植しました。
> 調査・移植の詳細は [`docs/migration_analysis.md`](docs/migration_analysis.md) を参照してください。

---

## 構成

```
BridgeLog/
├── electron/          # Electron main / preload / IPC（TypeScript, esbuild でビルド）
│   ├── main.ts        #   ウィンドウ・Backend ライフサイクル・終了確認
│   ├── preload.ts     #   contextBridge で window.bridge を最小公開
│   ├── backend.ts     #   uvicorn を子プロセスとして起動/停止/ヘルスチェック
│   └── ipc/handlers.ts#   dialog / shell / clipboard / settings
├── frontend/          # React + TypeScript + Vite（Renderer）
│   └── src/
│       ├── App.tsx
│       ├── components/TranscriptView.tsx
│       ├── features/transcription/useLiveTranscription.ts
│       ├── services/api.ts
│       └── types/bridge.ts
├── backend/           # FastAPI + Whisper（Python）
│   ├── main.py
│   ├── routes/        #   whisper.py（文字起こし）/ session.py（会議フォルダ）
│   ├── services/      #   transcriber / live_session / live_transcriber / exporter / runner / session_store
│   ├── config/transcription_terms.json
│   └── tests/
├── docs/migration_analysis.md
└── package.json
```

## 技術スタック

- Electron / React 18 / TypeScript / Vite
- FastAPI / WebSocket / faster-whisper / openai-whisper / ffmpeg

## 前提

- macOS（Apple Silicon 検証）
- Node.js 18+ / npm
- Python 3.x
- **ffmpeg / ffprobe**（`brew install ffmpeg`）— システム PATH の ffmpeg を使用します
  （環境変数 `BRIDGELOG_FFMPEG_DIR` で同梱ディレクトリを指定することも可能）

## セットアップ

```bash
cd /Users/hashimoto/vscode/_app/BridgeLog

# 1) Node 依存
npm install

# 2) Python 仮想環境
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

> `torch` / `openai-whisper` / `faster-whisper` は大型のため初回インストールに時間がかかります。
> 実推論を行わずサーバ起動・自動テストのみ確認する場合は、
> `pip install fastapi "uvicorn[standard]" python-multipart psutil` の軽量セットでも動作します。

## 起動（開発）

```bash
npm run dev
```

- Vite（Renderer, `http://localhost:5173`）と Electron が起動します。
- Electron が Python Backend（`uvicorn main:app`, `127.0.0.1:8000`）を**自動で起動**し、
  アプリ終了時に**自動で停止**します（Worker / ffmpeg の残留を防止）。
- `.venv` があればその Python を優先使用します。無い場合は `python3`。

補助スクリプト: `scripts/start_app.command`（ダブルクリック起動）。

## 使い方

1. 会議／セミナータイトルを入力（必須。録音中は編集不可）
2. マイGPT の URL を入力（`https://chatgpt.com/g/...`）
3. `＋` から資料を追加（PDF/txt/md/doc/ppt/画像。任意・重複不可）
4. 文字起こしファイル保存先を選択
5. 入力デバイス・モデル・遅延モードを選択
6. **文字起こし開始** → 長時間録音・逐次表示（確定文＋認識中）
7. **停止** → 残音声を最終確定し `transcript.txt` を保存
8. **マイGPTへ渡す** → ブラウザでマイGPT を開き、依頼文をクリップボードへコピー、
   TXT / 資料を Finder で確認できます

保存先には会議ごとのフォルダが作成されます:

```
20260806_タイトル/
├── transcript.txt          # 確定した最終全文
├── session.json            # メタデータ（元タイトル・GPT URL・状態・時刻）
├── attachments.json        # 資料一覧
└── diagnostics.log
```

## スクリプト

| コマンド | 内容 |
|---|---|
| `npm run dev` | 開発起動（Vite + Electron + Backend 自動起動） |
| `npm run build` | 型チェック → Electron ビルド → Renderer ビルド |
| `npm run dist` | `electron-builder` で macOS 向け配布物（dmg）を作成 |
| `npm run typecheck` | TypeScript 型チェック |
| `npm run test:backend` | Backend の Python ユニットテスト |

## テスト

```bash
# Backend
npm run test:backend
# または
cd backend && python3 -m unittest discover -s tests -p 'test_*.py'
```

移植した Whisper のユニットテスト（16件）:

- `test_live_session.py` — committed/partial 契約・segment 退避
- `test_whisper_transcriber.py` — Worker 監視・キャンセル・ffmpeg 回収・非ゼロ終了
- `test_whisper_runner.py` — 原子的保存・整形・出力ファイル
- `test_whisper_routes.py` — ジョブ状態機械（FastAPI 必須）

## 配布

```bash
npm run dist
```

`electron-builder` の設定は `package.json` の `build` セクションにあります。
`backend/` は `extraResources` として同梱されます（`.venv` / `__pycache__` を除外）。
配布先マシンにも Python と依存関係、ffmpeg が必要です。

## 実機確認について（重要）

Claude の実行環境では**実マイク入力・実 Whisper 推論・Electron GUI 操作を検証できません**。
以下はユーザー環境での最終確認をお願いします。

1. `npm run dev` でウィンドウが開くこと
2. マイク権限を許可し、`文字起こし開始`→話す→確定文が増え、認識中が置換されること
3. `停止`で `transcript.txt` が保存されること
4. `マイGPTへ渡す`でブラウザが開き、依頼文がクリップボードに入ること
5. 1時間以上の連続録音で committed 全文が増え続けること
6. アプリ終了後に Python / uvicorn / ffmpeg プロセスが残っていないこと
   （`ps aux | grep -E 'uvicorn|ffmpeg'`）

## セキュリティ

- `contextIsolation: true` / `nodeIntegration: false` / `sandbox: true`
- Renderer からは `window.bridge` 経由の最小 IPC のみ
- `openExternal` は `chatgpt.com` / `chat.openai.com` のみ許可（任意ドメインは開かない）
- CSP で接続先をローカル Backend に限定
