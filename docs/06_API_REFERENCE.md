# 06. API リファレンス

> `backend/routes/` に実在するエンドポイントのみを記載します。
> ホストとポートは `http://127.0.0.1:8765`（既定値。`KOENOTE_PORT` で変更可）です。

## 一覧

| Method | Path | 定義 |
|---|---|---|
| GET | `/api/health` | `backend/main.py` |
| POST | `/api/whisper/transcribe` | `backend/routes/whisper.py:292` |
| POST | `/api/whisper/upload` | `backend/routes/whisper.py:314` |
| GET | `/api/whisper/status/{job_id}` | `backend/routes/whisper.py:344` |
| POST | `/api/whisper/cancel/{job_id}` | `backend/routes/whisper.py:414` |
| POST | `/api/session/check_output` | `backend/routes/session.py:44` |
| POST | `/api/session/create` | `backend/routes/session.py:49` |
| POST | `/api/session/finalize` | `backend/routes/session.py:69` |
| POST | `/api/session/diagnostics` | `backend/routes/session.py:79` |
| POST | `/api/session/repair_audio` | `backend/routes/session.py:92` |
| WS | `/ws/live` | `backend/routes/whisper.py:671` |

CORS は `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` です
（`backend/main.py`。コメント: 「ローカルの Vite / Electron からのみアクセスされる想定」）。

---

## GET `/api/health`

**Request**: なし

**Response**

```json
{
  "status": "ok",
  "app": "KoeNote",
  "ffmpeg": "/opt/homebrew/bin/ffmpeg",
  "ffprobe": "/opt/homebrew/bin/ffprobe",
  "ffmpeg_ok": true
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `status` | string | 固定で `"ok"` |
| `app` | string | 固定で `"KoeNote"`。Electron が自 Backend かを判定する |
| `ffmpeg` / `ffprobe` | string \| null | 解決されたパス。見つからなければ `null` |
| `ffmpeg_ok` | boolean | 両方が解決できたか |

---

## POST `/api/whisper/transcribe`

ローカルパスのファイルを文字起こしします（非同期ジョブ）。

**Request**（`TranscribeRequest`）

| フィールド | 型 | 既定 | 必須 |
|---|---|---|---|
| `input_path` | string | — | ○ |
| `output_folder` | string \| null | `null` | |
| `decode_mode` | string | `"speed"` | |
| `write_to_file` | boolean | `true` | |

**Response**

```json
{ "job_id": "<uuid hex>" }
```

**エラー**: `input_path` が空 → `400`（`detail: "input_path を指定してください"`）

---

## POST `/api/whisper/upload`

アップロードしたファイルを文字起こしします（`multipart/form-data`）。

**Request**

| フィールド | 型 | 既定 | 必須 |
|---|---|---|---|
| `file` | file | — | ○ |
| `decode_mode` | form string | `"speed"` | |
| `write_to_file` | form string | `"1"` | |
| `output_folder` | form string | `""` | |

`write_to_file` は `"1"`, `"true"`, `"True"`, `"on"` のいずれかで真になります。

**Response**

```json
{ "job_id": "<uuid hex>" }
```

**エラー**

| 条件 | ステータス | detail |
|---|---|---|
| `file` 未指定 | 400 | `file が指定されていません` |
| 拡張子が `ALLOWED_EXTENSIONS` 外 | 400 | `対応していないファイル形式です` |

---

## GET `/api/whisper/status/{job_id}`

**Query**

| 名前 | 型 | 既定 |
|---|---|---|
| `offset` | int \| null | `null`（範囲外・負値は 0 に丸める） |

**Response**（主要フィールド）

| フィールド | 型 | 説明 |
|---|---|---|
| `status` | string | `queued` / `running` / `cancelling` / `done` / `error` / `cancelled` |
| `log` | string | 全ログ |
| `delta` | string | `offset` 以降のログ |
| `log_length` | int | `log` の長さ |
| `result` | any | 完了時の結果 |
| `exit_reason` | string \| null | 失敗分類 |
| `error` / `error_type` | string \| null | エラー内容 |
| `stage` | string \| null | 進行段階 |
| `stderr_tail` | string \| null | stderr の末尾 |
| `worker_pid` / `child_pid` / `process_pid` | int \| null | プロセス ID |
| `worker_alive` | boolean | ワーカー生存 |
| `returncode` | int \| null | 終了コード |
| `cancel_requested` | boolean | キャンセル要求済み |
| `current_position` / `segment` / `total_segments` | — | 進捗 |
| `peak_rss_mb` | — | ピーク RSS |
| `last_heartbeat_at` / `worker_heartbeat_at` / `progress_updated_at` / `child_output_at` / `stage_started_at` / `updated_at` | float | タイムスタンプ |

**エラー**: 未知の `job_id` → `404`（`detail: "not found"`）

ジョブの保持時間は `JOB_RETENTION_SECONDS = 6 * 60 * 60`（6 時間）です。

---

## POST `/api/whisper/cancel/{job_id}`

**Response**

```json
{ "status": "cancel_requested" }
```

**エラー**: 未知の `job_id` → `404`

---

## POST `/api/session/check_output`

**Request**

```json
{ "output_base": "/path/to/folder" }
```

**Response**

| フィールド | 型 | 説明 |
|---|---|---|
| `ok` | boolean | 存在 かつ 書き込み可 かつ 空き 200MB 超 |
| `exists` | boolean | ディレクトリとして存在するか |
| `writable` | boolean | 書き込み可能か（未作成なら親の可否） |
| `free_bytes` | int \| null | 空き容量 |
| `path` | string | 展開後の絶対パス |
| `reason` | string | `output_base` が空の場合 `"empty"` |

---

## POST `/api/session/create`

**Request**（`CreateSessionRequest`）

| フィールド | 型 | 既定 | 必須 |
|---|---|---|---|
| `title` | string | — | ○ |
| `output_base` | string | — | ○ |
| `gpt_url` | string | `""` | |
| `create_base_if_missing` | boolean | `true` | |

**Response**

```json
{
  "session_dir": "...",
  "transcript_path": "...",
  "segments_path": "...",
  "session_json_path": "...",
  "diagnostics_path": "...",
  "audio_path": "...",
  "transcript_filename": "transcript.txt",
  "session": { }
}
```

`session` の中身（= `session.json`）:

| キー | 値 |
|---|---|
| `app` | `"KoeNote"` |
| `title` | 入力されたタイトル |
| `safe_title` | サニタイズ後のタイトル |
| `gpt_url` | 入力された URL |
| `status` | 初期値 `"recording"` |
| `started_at` | ISO 8601 |
| `ended_at` | `null` |
| `transcript_path` | `"transcript.txt"` |
| `segments_path` | `"transcript_segments.json"` |
| `audio_path` | `"audio/recording.wav"` |

**エラー**

| 条件 | ステータス | detail |
|---|---|---|
| `title` が空 | 400 | `タイトルを入力してください` |
| `output_base` が空 | 400 | `保存先を指定してください` |
| `FileNotFoundError` | 400 | 例外メッセージ |
| `OSError` | 500 | `会議フォルダの作成に失敗しました: ...` |

---

## POST `/api/session/finalize`

**Request**（`FinalizeSessionRequest`）

| フィールド | 型 | 既定 |
|---|---|---|
| `session_dir` | string | — |
| `status` | string | `"done"` |
| `ended_at` | string \| null | `null`（未指定なら現在時刻） |

**Response**: 更新後の `session.json` の内容（dict）

**エラー**: `session_dir` が存在しない → `404` / `OSError` → `500`

---

## POST `/api/session/diagnostics`

**Request**

```json
{ "session_dir": "...", "message": "..." }
```

**Response**

```json
{ "ok": true, "diagnostics_path": "<session_dir>/diagnostics.log" }
```

> `frontend/src/services/api.ts` のコメントによれば、この経路は
> **異常記録には使いません**（Backend 停止時に失敗するため。0010 で Electron の
> ローカル I/O へ移行済み）。Backend が生きている前提の用途のために残されています。

---

## POST `/api/session/repair_audio`

強制終了でヘッダが古くなった `recording.wav` を実ファイル長から復旧します。

**Request**

```json
{ "session_dir": "..." }
```

**Response**

| 条件 | 内容 |
|---|---|
| 成功 | `{"ok": true, "audio_path": "...", "seconds": 123.45}` |
| ファイルなし | `{"ok": false, "reason": "not_found", "audio_path": "...", "seconds": 0.0}` |

**エラー**: `session_dir` が存在しない → `404` / `OSError` → `500`

---

## WebSocket `/ws/live`

### 接続手順

1. 接続後、**最初に `config` を text で送る**。
2. サーバが `ready`（新規）または `resumed`（再接続）を返す。
3. クライアントは PCM16LE のバイナリフレームを送り続ける。
4. `stop` を送ると確定処理が走り、`session_final` が返る。

最初のメッセージが `config` でない場合、サーバは
`{"type":"error","message":"最初に config を送信してください。"}` を返し `1003` で閉じます。

### クライアント → サーバ（text / JSON）

| `type` | フィールド | 説明 |
|---|---|---|
| `config` | 下表参照 | 最初に必ず送る |
| `stop` | — | 停止と確定処理を要求 |
| `resync` | — | `snapshot` の再送を要求 |
| `set_debug` | `value: boolean` | デバッグ表示の切り替え |
| `gap` | `samples: int` | 保持しきれなかった区間を無音で埋める |

`config` のフィールド（`LiveSessionConfig.from_payload`）:

| フィールド | 型 | 既定 | 備考 |
|---|---|---|---|
| `model` | string | `"small"` | `tiny` / `base` / `small` / `medium` 以外は既定へ |
| `delay_mode` | string | `"balanced"` | `low_latency` / `balanced` / `accuracy` |
| `chunk_seconds` | float | プリセット値 | 1.0〜30.0 にクランプ |
| `overlap_seconds` | float | プリセット値 | 0.0〜`chunk_seconds - 0.1` にクランプ |
| `write_to_file` | boolean | `true` | |
| `output_folder` | string | `""` | |
| `output_filename` | string \| null | `null` | |
| `send_mode` | string | `"chunks"` | `"pcm16"` を使う。`"full"` は**拒否される** |
| `sample_rate` | int | `16000` | 8000〜192000 にクランプ |
| `debug` | boolean | `false` | |
| `resume_session_id` | string | — | 再接続時に指定 |

> `send_mode: "full"` はサーバが
> `"send_mode='full' は O(T^2) のため realtime では使用しません。'pcm16' を使ってください。"`
> を返し `1003` で閉じます。

### クライアント → サーバ（binary）

PCM16LE mono のフレーム。フォーマットは `sample_rate`（既定 16000 Hz）、
1 サンプル 2 バイトです。

### サーバ → クライアント（JSON）

| `type` | 説明 |
|---|---|
| `ready` | 新規セッション確立 |
| `resumed` | 既存セッションへ再接続 |
| `snapshot` | 全文（`committed_text` / `partial_text`）。再接続直後と `resync` 時 |
| `update` | 確定テキストの差分 |
| `result` | 窓ごとの結果 |
| `metrics` | 窓の統計（`rms`, `inference_ms`, `lag_seconds`, `dropped_seconds` 等） |
| `heartbeat` | 生存通知と進捗 |
| `log` | ログ行 |
| `warning` | 警告 |
| `error` | エラー |
| `session_final` | 停止後の最終テキスト |

`ready` / `resumed` のフィールド:

| フィールド | 説明 |
|---|---|
| `session_id` | セッション ID（`uuid4().hex`） |
| `timestamp` | `%Y-%m-%dT%H:%M:%S` |
| `saved_path` | 保存先 |
| `audio_path` | 録音 WAV のパス（`write_to_file` 有効時） |
| `server_total_samples` | サーバが受理済みのサンプル数 |
| `committed_length` | 確定テキスト長 |
| `sample_rate` | `16000` |
| `chunk_seconds` / `overlap_seconds` | 窓の設定 |
| `heartbeat_interval_seconds` | ハートビート間隔 |
