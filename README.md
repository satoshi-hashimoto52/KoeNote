# KoeNote

会議・セミナーの**長時間文字起こし**を行い、事前登録したタイトル・マイGPT URL とともに、
文字起こし完了後に**マイGPT（ChatGPT の GPTs）へ渡す準備**を整える macOS 向け Electron デスクトップアプリ。

音声認識は **ローカルの Whisper**（File Trans: `openai-whisper` / Realtime: `faster-whisper`）で行い、
**OpenAI API は一切使用しません**（追加の従量課金なし）。

> Whisper 文字起こし機能は既存プロジェクト `my_launcher` から移植しました。
> 調査・移植の詳細は [`docs/migration_analysis.md`](docs/migration_analysis.md) を参照してください。

> 本アプリは以前 **BridgeLog** という名称でした。`docs/` 配下の受け入れ試験記録や Issue には
> 当時の名称が残っていますが、当時の記録として意図的に維持しています。

## ダウンロード

**[最新版をダウンロード（v0.1.0）](https://github.com/satoshi-hashimoto52/KoeNote/releases/tag/v0.1.0)**

| ファイル | 用途 |
|---|---|
| `KoeNote-0.1.0-arm64.dmg` | **通常はこちらを使用してください** |
| `KoeNote-0.1.0-arm64.zip` | DMG を使えない場合の代替 |
| `SHA256SUMS.txt` | ダウンロードしたファイルの検証用 |

### 前提

- **Apple Silicon（arm64）専用**です。Intel Mac では動作しません。
- **Homebrew 版の `ffmpeg` / `ffprobe` が必要**です（アプリへ同梱していません）。

  ```bash
  brew install ffmpeg
  ```

- **Whisper モデルは初回利用時に取得**します（`~/.cache/huggingface`、約 538MB）。
  初回だけインターネット接続が必要です。
- **File Trans（ファイル一括文字起こし）はパッケージ版では利用できません。**
  リアルタイム文字起こしは利用できます。

### インストール

1. `KoeNote-0.1.0-arm64.dmg` をダウンロードして開く
2. `KoeNote.app` を Applications フォルダへコピーする
3. **初回は `KoeNote.app` を右クリック →「開く」**を選ぶ
4. 確認画面でも「開く」を選ぶ
5. マイクへのアクセスを許可する

公証を行っていないため、ダブルクリックでは「開発元を検証できません」と表示されます。
右クリック →「開く」で起動できます。

### ダウンロードファイルの検証

`SHA256SUMS.txt` と DMG / ZIP を同じディレクトリへ置いて実行します。

```bash
shasum -a 256 -c SHA256SUMS.txt
```

`OK` が表示されれば、ファイルは改変されていません。

### 署名と公証について

- `.app` は **Apple Development 証明書で署名済み**です。
- **Apple による公証（notarization）は未実施**です。
- 公証には **Apple Developer Program への有料登録が必要**です。
- **現時点では対応を保留**しています。

## ドキュメント

設計・運用の詳細は [`docs/00_PROJECT_OVERVIEW.md`](docs/00_PROJECT_OVERVIEW.md) を入口としてください。

| 文書 | 内容 |
|---|---|
| [00 概要](docs/00_PROJECT_OVERVIEW.md) | 課題・機能・技術・実行/ビルド/テスト |
| [01 アーキテクチャ](docs/01_ARCHITECTURE.md) | 構成・データフロー・状態管理・通信 |
| [02 ディレクトリ構成](docs/02_DIRECTORY_STRUCTURE.md) | 各フォルダと主要ファイルの役割 |
| [03 技術スタック](docs/03_TECH_STACK.md) | ライブラリ一覧・用途・バージョン |
| [04 ビルドと実行](docs/04_BUILD_AND_RUN.md) | 実在するコマンド一覧 |
| [05 コーディング規約](docs/05_CODING_CONVENTIONS.md) | 命名・エラー処理・非同期・型 |
| [06 API リファレンス](docs/06_API_REFERENCE.md) | HTTP / WebSocket の仕様 |
| [07 データ](docs/07_DATABASE.md) | DB なし。ファイル永続化の仕様 |
| [08 設定](docs/08_CONFIGURATION.md) | 設定キー・環境変数・既定値 |
| [09 AI 開発ガイド](docs/09_AI_DEVELOPMENT_GUIDE.md) | 編集してよい場所・実装ルール |
| [10 既知の制約](docs/10_KNOWN_LIMITATIONS.md) | 未解決 Issue・技術的制約 |
| [CLAUDE.md](docs/CLAUDE.md) | Claude Code 向けの指示書 |
| [Issue 一覧](docs/issues/) | 0001〜0018 の調査・修正記録 |

## 画面（0015 / 0017 / 0018）

**320×530 を常用する前提のコンパクト UI** です。ウィンドウは手動で拡大・縮小できます。

- **タイトル**は表示ラベルを持たず、`タイトルを入力（必須）` の placeholder で入力します
  （アクセシビリティ名は `aria-label` に残しています）。
- **録音ステータス**は `● 録音中` を 1 つの表示にまとめ、この領域で最も目立たせます。
  音声時刻と文字起こし時刻はラベルを省略せず、**380px 以下では自動的に 2 行**へ切り替わります。
  保存先は時刻とは別の行に置きます。項目の隣の「ⓘ」は hover / フォーカス時だけ説明を出し、
  本体の文字情報に被らない位置へ表示します。
- **入力欄とボタン**はアプリ背景から明確に浮く配色にしています。色は `:root` の CSS 変数
  （`--field-*` / `--btn-neutral-*` / `--focus-ring` / `--disabled-opacity`）へ集約しています。
  キーボード操作時だけ `:focus-visible` で紫の枠とリングを出し、
  マウスクリック後にフォーカス枠を残しません。
- **ウィンドウの不透明度**を設定から **70〜100%**（5% 刻み、初期値 100%）で変更できます。
  `BrowserWindow.setOpacity()` を使うため、文字が薄くなることはありません。
  スライダー操作中はライブプレビューし、キャンセル / Escape / 背景クリックでは元の値へ戻ります。

## 入力デバイスの安全なフォールバック（0016）

Chromium は入力デバイスの `deviceId` を **origin ごとに異なる値へソルト**します。
開発版（`http://localhost:5173`）で保存した ID は、パッケージ版（`file://`）には存在しません。

そのため保存値をそのまま使わず、**必ず現在のデバイス一覧と照合**します。

| 状況 | 動作 |
|---|---|
| 保存 ID が一覧にある | その ID を使う |
| ID は無効だが**保存したデバイス名と一意に一致**する入力がある | 現在の ID へ**再解決**する |
| 一致しない／同名が複数ある | **既定の入力デバイス**を使い、通知を出す |

- フォールバックの通知は**表示から 8 秒で自動的に消えます**（録音は妨げません）。
  マイク権限の拒否など、ユーザー操作が必要な通知は消えません。
- **フォールバックしただけでは保存設定を書き換えません。**
  USB マイクを一時的に外しているだけの場合に、内蔵マイクを恒久設定にしないためです。
  設定画面で選び直して保存したときだけ `deviceId` と `deviceLabel` を永続化します。

### 設定の保存先

`~/Library/Application Support/KoeNote/koenote-settings.json`

旧 BridgeLog を使っていた場合、KoeNote 側にまだ設定が無い初回起動時に限り、
`~/Library/Application Support/BridgeLog/bridgelog-settings.json` から
`gptUrl` / `saveFolder` / `deviceId` / `deviceLabel` / `model` / `delayMode` /
`requestTemplate` / `transcriptHeight` / `windowOpacity` を引き継ぎます。
旧設定は読み取るだけで、変更も削除もしません。
KoeNote 側に既に設定がある場合は移行しません。

---

## 構成

```
KoeNote/
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
  （環境変数 `KOENOTE_FFMPEG_DIR` で同梱ディレクトリを指定することも可能）

## セットアップ

```bash
cd KoeNote   # クローンしたディレクトリ

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
- Electron が Python Backend（`127.0.0.1:8765`）を**自動で起動**し、
  アプリ終了時に**自動で停止**します（Worker / ffmpeg の残留を防止）。
  ポートは環境変数 `KOENOTE_PORT` で変更できます。
- `.venv` があればその Python を優先使用します。無い場合は `python3`。

補助スクリプト: `scripts/start_app.command`（ダブルクリック起動）。

## 使い方

1. 会議／セミナータイトルを入力（必須。録音中は編集不可）
2. マイGPT の URL を入力（`https://chatgpt.com/g/...`）
3. 文字起こしファイル保存先を選択
4. 入力デバイス・モデル・遅延モードを選択
   （**入力デバイス名を必ず確認してください。**詳細は「入力デバイスの確認」を参照）
5. **文字起こし開始** → 長時間録音・逐次表示（確定文＋認識中）
6. **停止** → 残音声を最終確定し `transcript.txt` を保存
7. **マイGPTを開く** → Google Chrome でマイGPT を開き、依頼文をクリップボードへコピー、
   TXT を Finder で確認できます

保存先には会議ごとのフォルダが作成されます:

```
20260806_タイトル/
├── transcript.txt          # 確定した最終全文
├── session.json            # メタデータ（元タイトル・GPT URL・状態・時刻）
└── diagnostics.log
```

## スクリプト

| コマンド | 内容 |
|---|---|
| `npm run dev` | 開発起動（Vite + Electron + Backend 自動起動） |
| `npm run build` | 型チェック → Electron ビルド → Renderer ビルド |
| `npm run package:backend` | Backend を PyInstaller で単体実行形式へ固める |
| `npm run package:mac` | Backend 同梱の `.app` と `.dmg` を `release/` へ作成 |
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

### segment の品質判定（無音の扱い）

無音判定は faster-whisper 本体と同じ**複合条件**で行う。

```
no_speech_prob > 0.6  かつ  avg_logprob < -1.0   -> 無音として除外
avg_logprob < -1.0                              -> 確信度不足として除外
compression_ratio > 2.4                          -> 繰り返し/ハルシネーションとして除外
ハルシネーション語句に一致                        -> 除外
```

`no_speech_prob` **単独**で segment を破棄してはいけない。realtime では窓が語の途中から
始まるため `no_speech_prob` が閾値をわずかに超えやすく（実測 0.62〜0.84）、単独判定では
`avg_logprob` -0.16 のような確信度の高い音声まで segment ごと捨てられる。
実測では 4 窓連続で約 16 秒の発話が失われ、無音境界のない発話ほど悪化した。

閾値は `backend/services/live_transcriber.py` の `NO_SPEECH_THRESHOLD` /
`LOGPROB_THRESHOLD` / `COMPRESSION_RATIO_THRESHOLD` で一元管理し、
`model.transcribe` へ渡す値と一致させる（ずれると「whisper が返したのに
こちらで捨てる」不整合になる）。回帰テストは `backend/tests/test_no_speech_filter.py`。

### 確定アルゴリズム（word 単位）

確定線 `stable_until` は次 window の開始時刻と**数学的に一致する**
（`window_end - overlap == window_start + step`）。そのため segment 単位で
「確定線までに終わったものだけ確定する」判定にすると、確定線をまたぐ segment は
保留のまま次 window に含まれず、テキストが失われる（`docs/issues/0001`）。
segment 単位で捨てる／丸ごと確定するどちらも欠落か重複を生むため、
**word timestamp 単位で確定する**。

判定の優先順:

1. **主判定 = 絶対音声時刻と確定済み音声境界**。
   `word.end <= committed_until - JITTER_MAX` の語は無条件に既確定とみなす。
2. **補助 = 曖昧帯（`committed_until ± JITTER_MAX`）内の位置決めのみ**。
   確定済み末尾（アンカー6語）と単調に接頭辞一致する分だけ既確定として除く。
   同じ語が密に連続する場合はテキストが全部一致してしまうため、
   一致長ではなく**時刻差が最小になる整合**を選ぶ。

文字列一致そのものを重複除去の根拠にはしない。「はい、はい」のような正当な
繰り返しを消してしまうため。句読点・空白（半角/全角）だけの差はアンカー照合時に
正規化して無視するが、長音符「ー」や「々」など語の一部になる文字は残す。

カーソル更新式:

```
pending なし: next_end = prev_end + step
pending あり: next_end = min(prev_end + step,
                            round((pending[0].start - CURSOR_MARGIN) * SR) + chunk)
共通        : next_end <= prev_end なら prev_end + MIN_ADVANCE
```

`word.start <= window_start` の語は「これより前から始まる window が今後存在しない」
ため強制確定する。これにより `pending[0].start > window_start` が保証され、
カーソルの前進が保証される（追従のみの実装はここで停滞する）。

停止時は `drain_on_stop` が確定端から音声終端まで走査して確定させる。
`finalize` は `partial_text` を無条件に追記しない（既確定分の二重書き込みになるため）。

word timestamp が取得できない segment は「segment 全体を1語とする擬似 word」として
通常の判定経路に載せる。全確定も全破棄もしない。擬似 word が既確定と重なる場合は
未確定区間だけを再推論して重なり自体を無くす。縮退はすべて reason コード付きで
`diagnostics.log` と WS `warning` に記録する。

### word timestamp のずれの再測定

`JITTER_MAX_SECONDS` と `CURSOR_MARGIN_SECONDS`（`backend/services/word_commit.py` で
一元管理）は実測に基づく**暫定的な設計値**である。

測定条件（現在の値の根拠）:

- モデル `small`、`word_timestamps=True`、`vad_filter=True`
- 音声2種: 句読点あり 22.5秒 / 無音の少ない長文 32.3秒（`say -v Kyoko` 生成）
- chunk 6 / 8 / 10 / 12 秒、窓開始 0〜8 秒、対応 768 語
- 結果: 中央値 0.000s / p95 0.080s / p99 0.180s / **実測最大 0.440s**
- よって実測最大を上回る **0.5s** を採用

モデル・話者・録音環境・推論パラメータを変えたら再測定して見直すこと。

```bash
say -v Kyoko -o sample.aiff "本日の会議を始めます。..."
ffmpeg -y -i sample.aiff -ac 1 -ar 16000 -f s16le sample.pcm
.venv/bin/python scripts/measure_word_jitter.py sample.pcm
.venv/bin/python scripts/measure_word_jitter.py sample.pcm --model medium --chunks 8 10 12
```

語の対応付けは「単調 + 時刻局所 + テキスト一致」の制約付き DP で行う。
テキスト一致だけで対応付けると繰り返し語を誤対応し、3 秒級の偽の外れ値が出る。

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
npm run typecheck     # tsc --noEmit
npm run test:unit     # Vitest（TypeScript）
npm run test:backend  # unittest（Python）
```

現在の件数（実測）:

| 対象 | ファイル数 | テスト件数 |
|---|---|---|
| Vitest（TypeScript） | 15 | **235** |
| Backend（Python） | 12 | **106** |

Backend のテストは 2 時間相当の連続動作テストを含み、約 8 秒で完走します。

- `test_live_session.py` — レガシー webm 経路の committed/partial 契約（回帰ガード）
- `test_live_session_pcm.py` — 窓のケイデンス・差分の正しさ・追いつき・
  **2時間連続でメモリと1周期コストが増えないこと**
- `test_word_commit.py` — word 単位確定の不変条件（全 word が厳密に1回・
  カーソルの前進・drain 後に未確定なし・確定済みは不変）。
  確定線をまたぐ segment / 境界語 / 無音なし長文 / 同一語の連続 /
  表記ゆれ / 縮退5種 / 1時間相当の状態量 / 実行の再現性を全プリセットで検証
- `test_pcm_stream.py` — リングバッファの絶対番号・破棄時 `None`・有界性・リサンプラ
- `test_wav_recorder.py` — 強制終了しても再生可能な WAV・ヘッダ復旧
- `test_live_ws_protocol.py` — 推論中も heartbeat が届く／差分のみ送る／resync／再接続の継続
- `test_whisper_transcriber.py` / `test_whisper_runner.py` / `test_whisper_routes.py` — ファイル文字起こし側

### 長時間の実測

```bash
# Backend を起動しておく（npm run dev でもよい）
.venv/bin/python scripts/live_soak.py --minutes 120 --speed 90 \
  --output-folder /tmp/koenote_soak
```

実 WebSocket で合成 PCM を流し、Backend の RSS 傾き・heartbeat 断・破棄音声・
録音ファイル長を判定する。`--speed 1` にすると実時間で回せる。

補助スクリプト:

- `scripts/audio_worklet_check.cjs` — 実 `file://` + 実 CSP で AudioWorklet が
  読み込めるかを確認する（キャプチャ方式を変えるときは必ず先に実行）
- `scripts/pcm_pipeline_check.cjs` — Renderer → Backend の PCM 経路を通しで検証


## 配布（macOS / Apple Silicon）

```bash
npm run package:mac
```

`release/mac-arm64/KoeNote.app`、`release/KoeNote-<version>-arm64.dmg`、
`release/KoeNote-<version>-arm64.zip` を生成します。

内訳:

| 段階 | コマンド | 内容 |
|---|---|---|
| 1 | `npm run package:backend` | Backend を PyInstaller で単体実行形式へ固める（`backend/packaging/dist/koenote-backend/`） |
| 2 | `npm run build` | 型チェック → Electron ビルド → Renderer ビルド |
| 3 | `electron-builder --mac --arm64` | `.app` と `.dmg` を作成 |

`electron-builder` の設定は `package.json` の `build` セクションにあります。

### 同梱するもの / しないもの

同梱する:

- Backend 実行形式（Python ランタイム、fastapi / uvicorn / faster-whisper / ctranslate2 /
  onnxruntime / av を含む）。パッケージ版は開発用 `.venv` もリポジトリも参照しません。

同梱しない（実行マシン側の前提）:

- **ffmpeg / ffprobe** — Homebrew 版が必要です（`/opt/homebrew/bin` または `/usr/local/bin`）。
  `brew install ffmpeg` で導入してください。見つからない場合は `/api/health` の
  `ffmpeg_ok` が `false` になります。
- **Whisper モデル** — 初回利用時に Hugging Face から `~/.cache/huggingface` へ取得します
  （small で約 464MB、tiny で約 75MB）。初回だけネットワークが必要です。

### 制限

- **ファイル一括文字起こし（File Trans）はパッケージ版では利用できません。**
  別プロセスの Python ワーカーを起動する実装のため、実行形式へ固めた環境では動きません。
  利用する場合は別途 Python を用意し、環境変数 `KOENOTE_PYTHON` にそのパスを指定してください。
  指定がない場合は黙って誤動作せず、明示的なエラーになります。
- **Backend のポートは既定 8765 です。** 他アプリと衝突する場合は環境変数 `KOENOTE_PORT`
  で変更できます。既に同じポートで応答があっても、`/api/health` が `app: "KoeNote"` を
  名乗らない限り再利用しません（無関係のサーバへ接続しないため）。

### 署名と Gatekeeper

`.app` は **Apple Development 証明書**で署名済みです。
**Apple による公証（notarization）は未実施**のため `spctl -a -t exec` は `rejected` になり、
Finder から初回起動すると「開発元を検証できません」の警告が出ます。

回避は macOS 標準の手順で行ってください（右クリック →「開く」、または
システム設定 →「プライバシーとセキュリティ」→「このまま開く」）。
Gatekeeper 自体の無効化や、成果物全体への `xattr -dr` は行わないでください。

公証には **Apple Developer Program への有料登録が必要**です。
**現時点では対応を保留**しています。

## 入力デバイスの確認

**録音開始前に、選択中の入力デバイス名を必ず確認してください。**

BlackHole や Loopback などの仮想ループバックデバイスを選ぶと、
マイクの肉声ではなく**システム音声（再生中の動画・会議アプリの音）が録音されます**。
これは会議音声を録る正当な用途でもあるため、アプリ側では自動判定しません。

確認箇所は次の 2 つ。どちらも `getUserMedia` が実際に許可したデバイス名（`track.label`）です。

- 画面右下のステータスバー `入力: …`
- 診断ログ 1 行目の `入力デバイス=…`

入力レベルメーターは再生音声でも振れるため、**音源の種別は判定できません。**
関連する改善提案は [`docs/issues/0005-input-device-misselection.md`](docs/issues/0005-input-device-misselection.md)。

## 検証済みの動作（2026-08-25 / コミット `ca0997b`）

手動受け入れ試験で確認した範囲。数値と手順の詳細は
[`docs/manual-acceptance-long-transcription.md`](docs/manual-acceptance-long-transcription.md) を参照。

- `small` / `balanced`（標準 10/2）で **68 分 00 秒の連続録音**を確認。
  20〜30 分で発生していた強制終了は再発なし
- **`dropped_seconds` = 0.0**（全 548 窓および `session_final`）。
  `untranscribed_seconds` / `forced_commit` / `pseudo_word` はいずれも 0
- **録音時間に比例したメモリ増加はない。**
  Python RSS は初回モデルロード時のピーク 998.8 MB から 60 分時点 519.1 MB へ推移し、単調増加なし
- `recording.wav` は正常再生可（16,000 Hz / 1ch / Int16）。
  wav 長と実録音時間の差 2.30 秒
- **実マイク入力での停止直前の末尾回収を確認**（E-3）。
  終了確認文が `transcript.txt` の末尾まで保存され、欠落・重複なし

未実施の項目（スリープ抑止、無音時の誤検知、Backend 異常終了時の通知など）は
チェックリスト側に「未実施」として明記しています。受け入れは条件付き合格の段階です。

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
