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
- FastAPI / WebSocket / faster-whisper / openai-whisper / numpy / ffmpeg

## 前提

- macOS（Apple Silicon 検証）
- Node.js 18+ / npm
- Python 3.x
- **ffmpeg / ffprobe**（`brew install ffmpeg`）— 音声ファイルからの文字起こしに使用します
  （リアルタイム文字起こしは ffmpeg を使いません）
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

## リアルタイム文字起こしの構造

長時間（1〜2時間）の連続録音に耐えるため、音声は **16kHz PCM16 の差分送信**で扱う。

```
Renderer                                  Backend (1 event loop, 4 tasks)
AudioContext(16kHz)                       _receiver     : PCM を追記するだけ（ブロックしない）
 └ AudioWorklet ─ 4KB/128ms ─ WS ──────>  _inference    : 直近1窓だけを別スレッドで推論
   (blob: URL から読込)                   _heartbeat    : 2秒ごとに生存信号
   ScriptProcessorNode へ自動fallback     _sender       : 送信を1本に集約（上限付きキュー）
```

設計上の要点:

- **送信量は 32KB/秒 固定**で、経過時間に依存しない（1時間で約115MB）。
- Whisper が見るのは常に**直近 1 窓だけ**。1 周期のコストは録音長に依存しない。
  これは `test_live_session_pcm.py` の「毎回の呼び出しが厳密に `chunk_samples` を受け取る」
  アサーションで機械的に保証している。
- 音声は容量 180 秒の**リングバッファ**に保持（約5.8MB）。推論が遅れても順に消化して
  取りこぼさず、容量を超えたときだけ明示的に破棄して `dropped_seconds` に計上・通知する。
- 確定済みテキストは再処理・再送しない。`update` は**差分のみ**を運び、
  基準がずれたら `resync` → `snapshot` で自己修復する。
- 受信ループは推論を待たない。これにより uvicorn の `pause_reading` 起因の
  keepalive 切断（close 1011）と受信キューの膨張が構造的に起きない。
- リアルタイム経路は **ffmpeg を使わない**（faster-whisper に numpy 配列を直接渡す）。
  ffmpeg は音声ファイルからの文字起こしにのみ必要。

### 異常検知と保全

- **録音音声は文字起こしとは別系統**で `<session>/audio/recording.wav` へ逐次保存。
  10 秒ごとにヘッダのサイズ欄を書き戻すので、強制終了しても再生できる
  （`POST /api/session/repair_audio` で実ファイル長から復旧も可能）。
- 確定テキストは `transcript.txt` へ即時 flush 追記。
- ウォッチドッグ 4 層: サーバ heartbeat 断（8秒）／キャプチャ停止（3秒）／
  文字起こし停止（60秒）／Backend プロセス異常終了（main プロセスが検知）。
- 異常時は **OS通知＋画面内ポップアップ＋警告音**の 3 経路で知らせ、
  ポップアップから「再接続」「録音を終了して保存」を選べる。
  異常の痕跡は `transcript.txt` の `[中断]` 行と `diagnostics.log` に残る。
- 異常切断は自動再接続（最大5回・指数バックオフ）。再接続中の音声は
  上限 60 秒のバッファに保持し、溢れた分は破棄したことを画面に出す。
- 録音中は `powerSaveBlocker` と `backgroundThrottling:false` でスリープ／
  タイマー間引きを抑止する（ただし蓋を閉じるとスリープする）。

## テスト

```bash
# Backend ユニットテスト（53件。2時間相当の連続動作テストを含み、約8秒で完走）
npm run test:backend
```

- `test_live_session.py` — レガシー webm 経路の committed/partial 契約（回帰ガード）
- `test_live_session_pcm.py` — 窓のケイデンス・差分の正しさ・追いつき・
  **2時間連続でメモリと1周期コストが増えないこと**
- `test_pcm_stream.py` — リングバッファの絶対番号・破棄時 `None`・有界性・リサンプラ
- `test_wav_recorder.py` — 強制終了しても再生可能な WAV・ヘッダ復旧
- `test_live_ws_protocol.py` — 推論中も heartbeat が届く／差分のみ送る／resync／再接続の継続
- `test_whisper_transcriber.py` / `test_whisper_runner.py` / `test_whisper_routes.py` — ファイル文字起こし側

### 長時間の実測

```bash
# Backend を起動しておく（npm run dev でもよい）
.venv/bin/python scripts/live_soak.py --minutes 120 --speed 90 \
  --output-folder /tmp/bridgelog_soak
```

実 WebSocket で合成 PCM を流し、Backend の RSS 傾き・heartbeat 断・破棄音声・
録音ファイル長を判定する。`--speed 1` にすると実時間で回せる。

補助スクリプト:

- `scripts/audio_worklet_check.cjs` — 実 `file://` + 実 CSP で AudioWorklet が
  読み込めるかを確認する（キャプチャ方式を変えるときは必ず先に実行）
- `scripts/pcm_pipeline_check.cjs` — Renderer → Backend の PCM 経路を通しで検証

### 既知の不具合（未修正）

窓の `stable_until` をまたぐ長い segment が、次の窓に含まれない場合に
テキストが失われることがある。レガシー webm 経路でも同一の結果になるため
PCM 化による回帰ではない（`test_live_session_pcm.py` の
`KnownSegmentBoundaryDefectTest` に再現を記録）。修正はコミット判定
アルゴリズムの再設計を伴う。

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
