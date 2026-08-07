# BridgeLog 移植調査 (Phase 1)

MyLauncher の Whisper 文字起こし機能を BridgeLog へ移植するための調査結果。

調査対象: `/Users/hashimoto/vscode/_app/my_launcher/launcher/apps/whisper`（および `backend/`, `tests/`）

---

## 1. MyLauncher Whisper 関連ファイル一覧

| ファイル | 役割 | 移植方針 |
|---|---|---|
| `launcher/apps/whisper/routes.py` (623行) | FastAPI ルーター。File Trans 用ジョブ管理 (`_jobs`, ステート機械, Worker 監視) と Realtime 用 WebSocket (`/ws/apps/whisper/live`) | **移植**（prefix と WS パスを BridgeLog 用に変更） |
| `services/transcriber.py` (535行) | File Trans 本体。長時間はセグメント分割、Worker(subprocess) 起動・監視・回収、途中保存/再開、ディスク容量チェック、RSS 監視 | **移植**（ffmpeg/モデル/Python 解決を BridgeLog 用に） |
| `services/runner.py` (280行) | Whisper Worker 本体（別プロセス）。`openai-whisper`+`torch` でモデル1回ロード→推論、用語正規化、整形、出力書き出し | **移植**（用語 config パス変更） |
| `services/live_session.py` (375行) | Realtime セッション。committed/partial 管理、ウィンドウ+オーバーラップ、停止時 finalize、TXT 追記 | **移植**（ffmpeg 解決を共通化） |
| `services/live_transcriber.py` (319行) | Realtime 推論。`faster-whisper` でモデルキャッシュ、webm→wav 変換、RMS/幻覚フィルタ | **移植** |
| `services/exporter.py` (56行) | 出力ディレクトリ作成、txt/raw/timestamped/segments.json 書き出し | **移植** |
| `services/file_utils.py` (25行) | `write_text_file` 原子的保存（temp→`os.replace`+fsync） | **移植** |
| `config/transcription_terms.json` | 用語正規化・initial_prompt | **移植** |
| `ui/WhisperApp.tsx` (981行) | Whisper 画面。File/RT タブ、MediaRecorder→WebSocket、ステータスポーリング | **参照のみ**（BridgeLog UI として作り直し。RT の WS/MediaRecorder ロジックは踏襲） |
| `ui/styles.css` | Whisper 画面スタイル | **参照のみ**（BridgeLog ダークテーマで作り直し） |
| `ffmpeg/bin/ffmpeg` (x86_64, 80MB) | 同梱 ffmpeg | **不採用**（システム ffmpeg を使用。後述） |
| `tests/test_live_session.py` | LiveSession の committed/partial テスト | **移植** |
| `tests/test_whisper_routes.py` | ルーターのステート機械テスト（fastapi 必須） | **移植** |
| `tests/test_whisper_runner.py` | atomic write / 整形 / 出力テスト（torch/whisper をスタブ） | **移植** |
| `tests/test_whisper_transcriber.py` | Worker 監視・キャンセル・回収テスト（Popen をモック） | **移植** |

---

## 2. 依存関係

### Python (`backend/requirements.txt`)
`fastapi`, `uvicorn[standard]`, `python-multipart`, `psutil`, `numpy`,
`openai-whisper`+`torch`（File Trans）, `faster-whisper`（Realtime）。
補助: `PyMuPDF` などは MyLauncher 他機能用で **BridgeLog では不要**。

> **注意**: `openai-whisper` / `faster-whisper` はローカル推論であり **OpenAI API 課金は一切発生しない**。要件「OpenAI API を使用しない」に適合。

### Frontend
React 18 + TypeScript + Vite。MyLauncher は `react-router-dom` を使用 → **BridgeLog は単一画面のため不要**。

---

## 3. ffmpeg の利用方法

- **File Trans**: `transcriber.resolve_ffmpeg_dir()` → 同梱 ffmpeg 優先、なければ `shutil.which("ffmpeg")`。`ffprobe` で長さ計測、`ffmpeg -ss -t` でセグメント抽出（mono/16kHz/pcm_s16le）。
- **Realtime**: `live_transcriber._convert_webm_to_wav()` で webm/mp4 → wav。`live_session._extract_tail_wav()` で `-sseof` により末尾ウィンドウを切り出し。
- **BridgeLog 方針**: 同梱バイナリ (x86_64/80MB) は Apple Silicon で Rosetta 動作となり非効率。**システム ffmpeg/ffprobe (Homebrew 7.1.1) を使用**。`resolve_ffmpeg_dir()` は環境変数 `BRIDGELOG_FFMPEG_DIR` 優先→未設定なら `None`（=PATH の ffmpeg）に変更し、MyLauncher の絶対パスを排除。将来同梱したい場合は `resources/ffmpeg/bin` を指す余地を残す。

---

## 4. Whisper モデルのロード方法

- **File Trans (`runner.py`)**: `whisper.load_model(model_spec, device)`。device は mps→cuda→cpu の順。モデルは Worker プロセスで **1回だけ** ロードし複数セグメントで再利用。
- **Realtime (`live_transcriber.py`)**: `faster_whisper.WhisperModel(name, device="cpu", compute_type="int8")`。`_model_cache` + `_model_lock` でプロセス内キャッシュ（1回ロード）。
- モデル指定: 環境変数 `WISPER_MODEL_NAME` / `WISPER_MODEL_PATH`、既定 `small`。**BridgeLog では `BRIDGELOG_MODEL_NAME` / `BRIDGELOG_MODEL_PATH` に改名**。

---

## 5. Backend 起動方法

- MyLauncher: `uvicorn main:app --reload --host 127.0.0.1 --port 8000`（`start_app.command` が Terminal で起動）。Vite が `/api`・`/ws` を 8000 へプロキシ。
- **BridgeLog 方針**: Electron main が uvicorn を **子プロセスとして spawn**（`127.0.0.1:8000`）、アプリ終了時に `SIGTERM`→猶予後 `SIGKILL`。開発時は Vite proxy を踏襲。

---

## 6. WebSocket 仕様（Realtime）

パス: `/ws/apps/whisper/live` → BridgeLog では `/ws/live`。

**クライアント→サーバ**:
1. 最初に `{"type":"config", model, delay_mode, chunk_seconds, overlap_seconds, write_to_file, output_folder, mime_type, debug_chunks, send_mode:"full"}`
2. 音声: バイナリ Blob（MediaRecorder webm/opus、`send_mode:"full"` は毎回「先頭からの全体」を1 Blob で送る＝ヘッダ保持のため）
3. 停止: `{"type":"stop"}`

**サーバ→クライアント**:
- `ready` `{session_id, saved_path}`
- `log` （診断文字列）
- `update` `{committed_text, partial_text, committed_until, stable_until, result_id, session_id, window_index, ...}`
- `session_final` `{committed_text, partial_text:"", text, result_id, saved_path}`
- `error` `{stage, error_type, message, ...}`

### committed / partial 契約 ★重要
仕様書が懸念していた「確定全文が増えない」問題について調査した結果:

- `live_session.py` の実装は **既に正しい**。
  - `committed_text` は **セッション開始から現在までの確定全文を累積**（`_join_transcript` で追記）。
  - `partial_text` は **最新ウィンドウの暫定結果のみ**（毎回置換）。
  - `finalize()`（stop 時）は残 partial を committed に確定し、`partial_text=""` を返す。
- テスト `test_full_windows_do_not_accumulate_or_wait_for_duration_growth` がこの契約を保証:
  `committed_text` が `"AB C"` まで増え、`finalize()` で `"AB CD"` になる。
- セグメント時刻が欠落した場合の退避（`classification_warning`）で認識文字列を失わない仕組みも存在。

→ **推測での大規模書き換えは行わず、この契約を維持したまま移植する。** Frontend は `committed_text` をスナップショット置換、`partial_text` を置換、表示は `committed_text + partial_text`。

---

## 7. 状態 API 仕様（File Trans）

- `POST /api/apps/whisper/transcribe` `{input_path, output_folder, decode_mode, write_to_file}` → `{job_id}`
- `POST /api/apps/whisper/upload`（multipart）→ `{job_id}`
- `GET /api/apps/whisper/status/{job_id}?offset=` → status, log/delta, result, worker_pid/child_pid, worker_alive, returncode, stage, peak_rss_mb, heartbeat 群, segment/total_segments
- `POST /api/apps/whisper/cancel/{job_id}`
- ステート機械: `queued→running→{done|error|cancelled}`。逆行禁止。`effective_status` は worker 生存時 `running`/`cancelling` を優先。
- BridgeLog では prefix を `/api/whisper` に変更。

---

## 8. 保存形式

- **File Trans** (`exporter`): `<出力先>/<YYYYMMDD_HHMMSS>/` に `*.txt`（整形済）, `*_raw.txt`, `*_timestamped.txt`, `*_segments.json`。書き出し後に存在検証してから done。
- **Realtime** (`live_session`): `<出力先>/meeting_<timestamp>.txt` に確定文のみ追記（途中仮説は書かない）。途中保存 `progress.json` と `segment_XXXX.json`（File Trans の再開用、`$TMPDIR/launcher_whisper_jobs/<job_id>`）。
- 原子的保存: `file_utils.write_text_file`（temp→fsync→`os.replace`）。
- **BridgeLog 追加**: 会議ごとフォルダ `YYYYMMDD_<safe_title>/` に `transcript.txt` / `transcript_segments.json` / `session.json` / `attachments.json` / `diagnostics.log`。`session_store.py` で管理。

---

## 9. MyLauncher 固有依存（分離・書き換えが必要）

| 依存 | BridgeLog での対応 |
|---|---|
| `react-router-dom` / `<Link to="/apps">` | 削除（単一画面） |
| `/api/pick_file`, `/api/pick_folder`（my_tool の Tkinter） | **Electron IPC ダイアログ**へ置換 |
| `main.py` の my_tool / sevenseg ルーター, CLI 実行, HTML 配信 | 除去（whisper + BridgeLog session のみ） |
| 同梱 ffmpeg 絶対パス (`apps/whisper/ffmpeg`) | 環境変数 or システム ffmpeg |
| 環境変数 `WISPER_*` | `BRIDGELOG_*` へ改名 |
| ジョブ一時ディレクトリ `launcher_whisper_jobs` | `bridgelog_jobs` へ改名 |
| import ルート `launcher.apps.whisper.*` | `services.*` / `routes.*`（backend をパッケージルート化） |

---

## 10. BridgeLog へそのまま移植できる部分

- 長時間対応の中核: セグメント分割、単一 Worker・モデル1回ロード、途中保存/再開、Worker/ffmpeg 回収、RSS 監視、ディスク容量チェック、診断ログ、ステート機械。
- Realtime の安定化: committed/partial 分離、ウィンドウ+オーバーラップ、finalize、再送重複防止（`result_id`）、診断ログ上限、送信 Blob の非蓄積（`send_mode:"full"` は最新1 Blob）。
- 原子的保存 / 出力検証。
- 全ユニットテスト（import パス調整のみ）。

## 11. 書き換えが必要な部分

- FastAPI ルーター prefix / WS パス / CORS。
- ffmpeg・モデル・Python の解決関数（絶対パス排除、環境変数化）。
- Frontend（単一画面・ダークテーマ・Electron IPC 化）。
- Electron main/preload（Backend spawn、dialog、shell、clipboard）。
- BridgeLog セッション（会議フォルダ、session.json、マイGPT 受け渡し）。

---

## 12. 実機確認できない項目（Claude 実行環境）

- 実マイク入力での Realtime 文字起こし（`getUserMedia` はブラウザ/Electron 実機が必要）。
- torch / openai-whisper / faster-whisper の実推論（大型・重量。Python 3.14 のホイール可用性はユーザー環境依存）。

→ これらはユーザーが実機確認。自動テスト（モック）・型チェック・構文チェック・build・Backend import/health は Claude 側で実施する。
