# 05. コーディング規約

> Linter / Formatter の設定ファイルはリポジトリに**存在しません**。
> 本書は既存コードから読み取れる実際の慣習のみを記載します。

## 命名規則

### TypeScript

| 対象 | 規則 | 実例 |
|---|---|---|
| 定数（モジュール公開） | `SCREAMING_SNAKE_CASE` | `ALLOWED_GPT_HOSTS`, `BACKEND_PORT`, `MIGRATED_KEYS` |
| 関数 | `camelCase` | `classifyExit`, `computeEffectiveHeight`, `buildRequestText` |
| 型 / インターフェース | `PascalCase` | `BridgeApi`, `LiveStatus`, `BackendExitInfo` |
| React コンポーネント | `PascalCase` + `.tsx` | `SettingsModal.tsx`, `TranscriptView.tsx`, `GearIcon.tsx` |
| 純関数モジュール | `camelCase` + `.ts` | `recordButton.ts`, `settingsDraft.ts`, `transcriptHeight.ts` |
| テスト | `<対象>.test.ts` | `watchdog.test.ts`, `settingsMigration.test.ts` |

### Python

| 対象 | 規則 | 実例 |
|---|---|---|
| モジュール | `snake_case.py` | `live_session.py`, `wav_recorder.py` |
| 関数 | `snake_case` | `create_meeting_directory`, `repair_wav_header` |
| クラス | `PascalCase` | `LiveSession`, `CrashSafeWavWriter`, `RecorderRegistry` |
| 非公開関数 | 先頭 `_` | `_now_iso`, `_clamp_float`, `_classify_failure` |
| 定数 | `SCREAMING_SNAKE_CASE` | `SAMPLE_RATE`, `DELAY_PRESETS`, `TRANSCRIPT_FILENAME` |
| Pydantic モデル | `<動作>Request` | `CreateSessionRequest`, `RepairAudioRequest` |
| テスト | `test_<対象>.py` | `test_word_commit.py`, `test_wav_recorder.py` |

## ファイル配置

| 種別 | 配置先 | 根拠 |
|---|---|---|
| React コンポーネント | `frontend/src/components/` | 既存 4 ファイル |
| UI から分離した純関数 | `frontend/src/components/` または `features/transcription/` | `transcriptHeight.ts`, `watchdog.ts` |
| 文字起こしロジック | `frontend/src/features/transcription/` | 既存 6 ファイル |
| Backend API 呼び出し | `frontend/src/services/api.ts` | 単一ファイルに集約 |
| Electron IPC | `electron/ipc/` | `handlers.ts` に `ipcMain.handle` を集約 |
| FastAPI ルート | `backend/routes/` | `APIRouter` 単位 |
| ドメインロジック | `backend/services/` | ルートから分離 |

**テストは実装と同じディレクトリに置きます**（TypeScript）。
Python のテストは `backend/tests/` に集約されています。

## コメントルール

観察された特徴です。

- **コメントは日本語**で書かれています。
- **「なぜそうしたか」を書く**傾向が強く、動作の言い換えは少数です。

```ts
// PCM フレームは 4KB 程度。既定の 16MiB より遥かに小さい上限にしておくことで、
// 将来また「全音声を送り直す」実装が入っても 25 分後の時間爆弾ではなく即座に失敗する。
```

- **Issue 番号を根拠として引用**します（`（0013）`, `（0010）`, `0015:` など）。

```python
# WAV パス単位で所有権を取る。再接続が重なっても書き込み可能な
# recorder は 1 つだけになり、古い recorder はここで閉じられる（0013）。
```

- Python のモジュール／関数には docstring（`"""..."""`）を付けるものがあります。
- TypeScript の公開関数には JSDoc（`/** ... */`）を付けるものがあります。

## エラー処理

### TypeScript

| パターン | 用途 | 実例 |
|---|---|---|
| `try { } catch { }`（バインドなし） | 失敗しても既定値で続行 | `electron/ipc/handlers.ts` の `readSettings` |
| `try { } catch (error) { }` | 失敗内容を使う場合 | `electron/backend.ts` |
| `.catch(() => {})` | 失敗を無視して続行 | `frontend/src/App.tsx:115` |
| `{ ok: boolean; reason?: string }` を返す | 例外を投げず結果で返す | `bridge.openExternal`, `bridge.appendDiagnostics` |

壊れた入力に対しては**既定値へフォールバックし、呼び出し側を止めない**方針が
コメントに明示されています（例: `/* 壊れていても既定へフォールバック */`）。

### Python

| 例外 | 出現数 | 用途 |
|---|---|---|
| `except Exception as exc` | 14 | 汎用。ログや診断へ回す |
| `except Exception` | 10 | 続行を優先する箇所 |
| `except OSError as exc` | 8 | ファイル I/O |
| `except OSError` | 3 | 同上（詳細不要な場合） |
| `except (TypeError, ValueError)` | 2 | 入力値の変換 |
| `except WebSocketDisconnect` | 1 | WS 切断 |

FastAPI のルートでは、失敗を `HTTPException` に変換して返します。

```python
except FileNotFoundError as exc:
    raise HTTPException(status_code=400, detail=str(exc))
except OSError as exc:
    raise HTTPException(status_code=500, detail=f"会議フォルダの作成に失敗しました: {exc}")
```

`detail` は**日本語**です。Renderer 側は `payload?.detail` を読んで表示します
（`frontend/src/services/api.ts`）。

## 非同期処理

| 環境 | 方式 | 補足 |
|---|---|---|
| TypeScript | `async` / `await`（75 箇所） | `ipcRenderer.invoke` は Promise を返す |
| Python | `async def`（7 箇所） | WebSocket ハンドラと送信ループ |
| Python（ブロッキング処理） | `asyncio.to_thread`（3 箇所） | 推論・WAV 解放をイベントループから逃がす |
| Python（スレッド共有状態） | `threading.Lock` | `backend/routes/whisper.py` の `_jobs_lock` |

## 型定義

| 項目 | 実態 |
|---|---|
| `strict` | `true`（`tsconfig.json`） |
| `noUnusedLocals` / `noUnusedParameters` | `true` |
| `noFallthroughCasesInSwitch` | `true` |
| `noEmit` | `true`（型チェック専用） |
| 状態の表現 | **判別可能ユニオン**を使う（`LiveStatus` の `kind`） |
| 定数集合 | `as const` + `typeof` で型を導出（`MIGRATED_KEYS`） |
| `window` 拡張 | `declare global` で `Window.bridge` を宣言（`frontend/src/types/bridge.ts`） |

Python には型注釈が部分的に付いています（`def repair_wav_header(path) -> float:` など）。
`mypy` などの型チェッカ設定は**存在しません**。

## セキュリティ上の慣習

| 項目 | 実装 | 根拠 |
|---|---|---|
| Renderer から Node API を使わせない | `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` | `electron/main.ts` |
| 公開する IPC は最小限 | `contextBridge.exposeInMainWorld` で明示列挙 | `electron/preload.ts` |
| 外部 URL は許可ホストのみ | `chatgpt.com` / `chat.openai.com` かつ `https:` | `electron/ipc/openExternal.ts` |
| シェルを介さない外部起動 | `execFile('open', [...])`（`shell: true` を使わない） | `electron/ipc/openExternal.ts` |
| CSP | `default-src 'self'` ほか。`connect-src` は `127.0.0.1` のみ | `frontend/index.html` |
| Backend の再利用判定 | `/api/health` の `app` が `KoeNote` の場合のみ | `electron/backend-lifecycle.ts` |
