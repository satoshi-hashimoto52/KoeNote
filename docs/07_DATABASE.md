# 07. データベース

## 結論

**このプロジェクトにデータベースは存在しません。**

## 根拠

リポジトリ全体を検索した結果です。

| 検索対象 | 結果 |
|---|---|
| `sqlite` / `postgres` / `mysql` / `mongo` / `redis` | 該当なし |
| `sqlalchemy` / `alembic` / `prisma` | 該当なし |
| `CREATE TABLE` | 該当なし |
| `migration` | `electron/ipc/settingsMigration.ts`（**設定ファイルの移行**であり DB マイグレーションではない） |
| `.db` ファイル | 該当なし |
| `backend/requirements.txt` の DB ドライバ | 記載なし |

ORM、テーブル定義、スキーマ、マイグレーションはこのプロジェクトでは確認できません。

## 代替となる永続化

データはすべて**ファイルシステム上の JSON / テキスト / WAV** として保存されます。

### セッションフォルダ

`backend/services/session_store.py` の定数に基づきます。
保存先はユーザーが設定画面で指定します（`saveFolder`）。

```text
<saveFolder>/<YYYYMMDD>_<safe_title>[_NN]/
├── session.json                 # メタデータ
├── transcript.txt               # 文字起こし本文
├── transcript_segments.json     # セグメント情報
├── diagnostics.log              # 異常記録
└── audio/
    └── recording.wav            # 録音音声（PCM16LE mono 16kHz）
```

フォルダ名が衝突した場合は `_01`, `_02` … と連番が付きます
（`create_meeting_directory` の `FileExistsError` ハンドリング）。

### session.json のスキーマ

| キー | 型 | 初期値 |
|---|---|---|
| `app` | string | `"KoeNote"` |
| `title` | string | 入力値（元の文字列を保持） |
| `safe_title` | string | サニタイズ後（最大 80 文字） |
| `gpt_url` | string | 入力値 |
| `status` | string | `"recording"` → `finalize` で `"done"` 等へ |
| `started_at` | string | ISO 8601 |
| `ended_at` | string \| null | `null` → `finalize` で設定 |
| `transcript_path` | string | `"transcript.txt"`（相対パス） |
| `segments_path` | string | `"transcript_segments.json"`（相対パス） |
| `audio_path` | string | `"audio/recording.wav"`（相対パス） |

書き込みは `write_text_file` 経由で、`json.dumps(..., ensure_ascii=False, indent=2)` に
改行を付けた形式です。

> `docs/issues/0004-transcript-segments-json-missing.md` に、
> `segments_path` が指すファイルが生成されない事象が「未調査（仕様確認が必要）」として
> 記録されています。

### 録音 WAV

| 項目 | 値 | 根拠 |
|---|---|---|
| 形式 | RIFF / WAVE, PCM16LE | `backend/services/wav_recorder.py:_wav_header` |
| サンプルレート | 16000 Hz | `backend/services/pcm_stream.py:12` |
| チャンネル | 1（mono） | `pcm_stream.py` docstring |
| ヘッダ更新 | 書き込み中も定期的に更新 | `CrashSafeWavWriter` |
| 修復 | `repair_wav_header(path)` で実ファイル長から復旧 | `wav_recorder.py:33` |

`CrashSafeWavWriter` の設計意図は「強制終了しても再生可能な WAV」を残すことです
（`README.md` のテスト説明、`backend/tests/test_wav_recorder.py`）。

### アプリ設定

| 項目 | 値 |
|---|---|
| パス | `~/Library/Application Support/KoeNote/koenote-settings.json` |
| 書き込み方式 | 一時ファイルへ書いて `rename`（アトミック） |
| 定義 | `electron/ipc/handlers.ts` |

保存されるキー（`electron/ipc/settingsMigration.ts` の `MIGRATED_KEYS` および
`frontend/src/App.tsx` の `setSettings` 呼び出しより）:

| キー | 用途 |
|---|---|
| `gptUrl` | マイGPT の URL |
| `saveFolder` | セッションの保存先 |
| `deviceId` | 入力デバイス ID（origin ごとにソルトされる。0016） |
| `deviceLabel` | 選択時のデバイス名（0016） |
| `model` | Whisper モデル |
| `delayMode` | 遅延プリセット |
| `requestTemplate` | 依頼文テンプレート |
| `transcriptHeight` | 文字起こし欄の高さ |
| `windowOpacity` | ウィンドウの不透明度 0.70〜1.00（0018） |

書き手は Electron main だけです。詳細は
[`08_CONFIGURATION.md`](08_CONFIGURATION.md) を参照してください。

### インメモリの状態

永続化されない状態です。

| 状態 | 実体 | 寿命 |
|---|---|---|
| リアルタイムセッション | `LiveSessionRegistry`（`backend/services/live_registry.py`） | Backend プロセス内。WS 切断後も一定期間保持し再接続で復帰 |
| File Trans ジョブ | `_jobs` dict + `threading.Lock`（`backend/routes/whisper.py`） | `JOB_RETENTION_SECONDS = 6 * 60 * 60`（6 時間） |
