# 10. 既知の制約と未解決事項

> `docs/issues/`、`README.md`、ソースコードのコメントに**明記されている**内容のみを記載します。
> 推測による制約は含みません。

## TODO / FIXME / HACK コメント

リポジトリ全体を検索した結果です。

| マーカー | 件数 |
|---|---|
| `TODO` | **0 件** |
| `FIXME` | **0 件** |
| `HACK` | **0 件** |
| `XXX` | **0 件** |

コード内に未処理タスクを示すマーカーはありません。

## Deprecated の言及

| 箇所 | 内容 |
|---|---|
| `frontend/src/features/transcription/pcmCapture.ts:140` | `ScriptProcessorNode はメインスレッド動作で deprecated なので主経路にはしない。` |

主経路は AudioWorklet です。`ScriptProcessorNode` はフォールバックとして残されています。

## 未解決の Issue

`docs/issues/` の「状態」行に基づきます。

| Issue | タイトル | 状態 | 影響（文書記載） |
|---|---|---|---|
| 0001 | segment 境界でのテキスト欠落・重複 | **対応中** | 文字起こし内容の欠落 |
| 0002 | 停止時に未送信の PCM が破棄される | **未対応** | リアルタイム文字起こしの末尾テキストと `recording.wav` の末尾 |
| 0003 | 停止時に AudioWorklet の書きかけフレームがフラッシュされない | **未対応** | `transcript.txt` と `audio/recording.wav` の末尾、最大約 128 ms |
| 0004 | `session.json` の `segments_path` が指すファイルが生成されない | **未調査**（仕様確認が必要） | `session.json` を読む外部処理・将来の機能 |
| 0005 | 入力デバイスの誤選択に気付けない | **未対応**（改善提案） | 録音内容そのもの。気付かないまま長時間の録音が無価値になる |
| 0014 | `session.json` が done になった後も WAV の最終処理が続く | **未調査** | データ破損は確認されていない。非ブロッキング |

## 解決済みの Issue

| Issue | タイトル | 状態 |
|---|---|---|
| 0006 | マイGPT を Google Chrome で開く | 解決 |
| 0007 | 「資料」機能を削除する | 解決 |
| 0008 | 文字起こし欄の高さを可変にする | 再オープン → 解決 |
| 0009 | ウィンドウ最小サイズの縮小とレスポンシブ強化 | 解決 |
| 0010 | Backend 異常終了時に diagnostics.log を保存できない | 解決 |
| 0011 | `transcription_stalled` の疑わしい発火 | 解決 |
| 0012 | Backend 再起動要求が二重実行される | 解決 |
| 0013 | アプリ終了時に WAV ヘッダが巻き戻る | 解決 |
| 0015 | 320px 幅を常用するためのコンパクト UI | 解決 |
| 0016 | origin 変更で保存済み入力デバイスIDが無効になり録音を開始できない | 解決 |

## 技術的制約（README 記載）

### パッケージ版の機能制限

> **ファイル一括文字起こし（File Trans）はパッケージ版では利用できません。**
> 別プロセスの Python ワーカーを起動する実装のため、実行形式へ固めた環境では動きません。
> 利用する場合は別途 Python を用意し、環境変数 `KOENOTE_PYTHON` にそのパスを指定してください。
> 指定がない場合は黙って誤動作せず、明示的なエラーになります。

実装は `backend/services/transcriber.py` の `resolve_python()` です。
`sys.frozen` が真かつ `KOENOTE_PYTHON` 未設定なら `RuntimeError` を送出します。
回帰テストは `backend/tests/test_resolve_python_frozen.py` にあります。

### 同梱しない外部依存

| 依存 | 前提 |
|---|---|
| ffmpeg / ffprobe | Homebrew 版が必要（`/opt/homebrew/bin` または `/usr/local/bin`）。未解決なら `/api/health` の `ffmpeg_ok` が `false` |
| Whisper モデル | 初回利用時に Hugging Face から `~/.cache/huggingface` へ取得。small 約 464MB、tiny 約 75MB。**初回だけネットワークが必要** |

### ポート

> Backend のポートは既定 8765 です。他アプリと衝突する場合は環境変数 `KOENOTE_PORT`
> で変更できます。既に同じポートで応答があっても、`/api/health` が `app: "KoeNote"` を
> 名乗らない限り再利用しません。

### 署名と Gatekeeper

> 現状の `.app` はローカルのキーチェーンにある **Apple Development 証明書**で署名され、
> 公証（notarization）は行っていません。そのため `spctl -a -t exec` は `rejected` になり、
> Finder から初回起動すると「開発元を検証できません」の警告が出ます。

> 警告なしで配布したい場合は Developer ID 証明書と公証が必要です。

### スリープ

> 録音中は `powerSaveBlocker` と `backgroundThrottling:false` でスリープ／
> タイマー間引きを抑止する（**ただし蓋を閉じるとスリープする**）。

### 入力デバイス（Issue 0016）

Chromium は `MediaDeviceInfo.deviceId` を **origin ごとに異なる値へソルト**します。
開発版（`http://localhost:5173`）で保存した ID はパッケージ版（`file://`）には存在せず、
`exact` 指定すると `OverconstrainedError` になります。

現在は保存値を現在のデバイス一覧と照合し、無効なら既定入力へフォールバックして
通知します（8 秒で自動消去）。フォールバックだけでは保存設定を書き換えないため、
恒久的に警告を消すには設定画面で選び直して保存する必要があります。

### 入力デバイス

> BlackHole や Loopback などの仮想ループバックデバイスを選ぶと、
> マイクの肉声ではなく**システム音声が録音されます**。
> これは会議音声を録る正当な用途でもあるため、アプリ側では自動判定しません。

> 入力レベルメーターは再生音声でも振れるため、**音源の種別は判定できません。**

## 設計上の制約（コード内コメント）

| 制約 | 根拠 |
|---|---|
| `send_mode: "full"` は使用できない | 「O(T^2) のため realtime では使用しません」（`backend/routes/whisper.py`） |
| WebSocket の最大メッセージサイズは 1 MiB | `--ws-max-size 1048576`。「将来また『全音声を送り直す』実装が入っても 25 分後の時間爆弾ではなく即座に失敗する」 |
| 音声バッファの猶予は 180 秒 | `BUFFER_CAPACITY_SECONDS`。「180秒で約5.8MB（録音長に依存しない）」 |
| 遅延 30 秒超で overlap を捨てる | `LAG_CATCHUP_THRESHOLD_SECONDS`。「音声を落とす前の安全弁」 |
| 診断ログの保持は 200 行 | `MAX_DEGRADE_LOG_LINES`。「状態量を有界に保つ」 |
| File Trans ジョブの保持は 6 時間 | `JOB_RETENTION_SECONDS` |
| word 単位確定の設計値は暫定 | 「暫定的な設計値。faster-whisper small + word_timestamps=True で…」（`backend/services/word_commit.py:25`） |

## プロジェクト運用上の不足

| 項目 | 状態 |
|---|---|
| CI / CD（GitHub Actions 等） | **存在しない**。自動検証は行われない |
| Dockerfile / docker-compose | **存在しない** |
| ESLint / Prettier / EditorConfig | **存在しない** |
| `LICENSE` ファイル | **存在しない**（`package.json` にも `license` フィールドなし） |
| `CONTRIBUTING.md` | **存在しない** |
| PR / Issue テンプレート | **存在しない**（`.github/` なし） |
| `mypy` などの Python 型チェッカ設定 | **存在しない** |
| `backend/requirements.txt` のバージョンピン | **なし**（すべて指定なし） |
| PyInstaller の `requirements.txt` 記載 | **なし**（`package:backend` で使用されるが依存として宣言されていない） |
| Windows / Linux 向けビルド設定 | **存在しない**（`build.mac` のみ） |

## 対象プラットフォーム

| 項目 | 値 |
|---|---|
| OS | macOS のみ（`package.json` `build.mac`） |
| アーキテクチャ | arm64 のみ（`target` の `arch`） |

Intel Mac（x64）向けの設定はこのプロジェクトでは確認できません。
