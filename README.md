# BridgeLog

会議・セミナーの**長時間文字起こし**を行い、事前登録したタイトル・マイGPT URL とともに、
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
# Backend ユニットテスト（53件。2時間相当の連続動作テストを含み、約8秒で完走）
npm run test:backend
```

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
  --output-folder /tmp/bridgelog_soak
```

実 WebSocket で合成 PCM を流し、Backend の RSS 傾き・heartbeat 断・破棄音声・
録音ファイル長を判定する。`--speed 1` にすると実時間で回せる。

補助スクリプト:

- `scripts/audio_worklet_check.cjs` — 実 `file://` + 実 CSP で AudioWorklet が
  読み込めるかを確認する（キャプチャ方式を変えるときは必ず先に実行）
- `scripts/pcm_pipeline_check.cjs` — Renderer → Backend の PCM 経路を通しで検証


## 配布

```bash
npm run dist
```

`electron-builder` の設定は `package.json` の `build` セクションにあります。
`backend/` は `extraResources` として同梱されます（`.venv` / `__pycache__` を除外）。
配布先マシンにも Python と依存関係、ffmpeg が必要です。

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
