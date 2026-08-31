# Issue #0016: origin が変わると保存済み入力デバイスIDが無効になり、録音を開始できない

- 状態: **解決**（純関数化 + 失敗するテストを先に追加して修正。Vitest 235 件 / Backend 106 件で検証）
- 起点コミット: `43fdd31`（`build: rename and package KoeNote for macOS arm64`）でパッケージ版として顕在化。
  不具合自体は `deviceId` を保存するようになった時点から潜在していた
- 種別: bug（録音が一切開始できない）
- 重要度: **中**
- 影響: パッケージ版（`file://`）での録音開始。開発版で設定を作ったユーザー全員

---

## 症状

パッケージ版 `KoeNote.app` で録音を開始できない。

- 開発版（`http://localhost:5173`）で保存した `deviceId` が、パッケージ版（`file://`）では無効
- 無効な `deviceId` を `getUserMedia` へ `exact` 指定するため `OverconstrainedError`
- `OverconstrainedError.message` が空文字のため、**エラーバナーが空欄**になる
- セッション作成のあとにマイク取得が失敗するため、
  `session.json` だけを含む `status: "recording"` の空セッションが残る
- 設定モーダルの入力デバイス欄も空欄になる

---

## 確定した原因

Chromium は `MediaDeviceInfo.deviceId` を **origin ごとに異なる値へソルト**する。

| 実行形態 | origin | 保存済み `4611f138…` |
|---|---|---|
| 開発版 `npm run dev` | `http://localhost:5173` | 有効 |
| パッケージ版 `.app` | `file://` | **存在しない** |

`frontend/src/App.tsx` は保存値を現在のデバイス一覧と照合せずに渡し、
`frontend/src/features/transcription/useLiveTranscription.ts` が
`getUserMedia({ audio: { deviceId: { exact: … } } })` を実行するため、
一覧に無い ID では必ず `OverconstrainedError` になる。

### 実測（同一ページを 2 つの origin で読み込み、同じ物理デバイスを列挙）

```text
file://                  → default, b585595e…, 2315e3b3…, 409c4678…, eb9c3e5a…
http://127.0.0.1:45123   → default, e6bfda1f…, e78c88c4…, 623e5e83…, 33b711cd…
```

ラベルは両者とも同一（`MacBook Airのマイク (Built-in)` ほか 4 件）だが、
`deviceId` は完全に別物。`groupId` も同様に origin 依存の可能性があるため、
**安定識別子として前提にしない**。

### `file://` 配下での `exact` 指定の結果

| 指定した deviceId | 結果 |
|---|---|
| 保存済み（開発版由来） | `OverconstrainedError`（message は空文字） |
| `file://` で取得した ID | `OK`（track label = `MacBook Airのマイク (Built-in)`） |

---

## 切り分け結果（いずれも正常。本 Issue の原因ではない）

| 対象 | 結果 |
|---|---|
| Backend（同梱バイナリ、port 8765） | 実音声で文字起こし成功。48kHz リサンプルも正常 |
| セッション作成 API | フォルダと `session.json` を正しく生成 |
| Renderer → Backend の PCM 経路 | `scripts/pcm_pipeline_check.cjs` が本番同条件（`file://` + 実 CSP + blob: AudioWorklet）で OK |
| AudioWorklet の CSP 読み込み | 成功 |
| マイク権限（OS） | `granted` |
| ffmpeg 解決 | `ffmpeg_ok: true` |

---

## 証拠として保全する既存フォルダ

ユーザーが 4 回試行し、いずれも失敗した記録。**削除・変更しない。**
（パスはユーザーのデスクトップ配下。公開用に親ディレクトリを省略して記載する）

| フォルダ | `started_at` | `status` | ファイル構成 |
|---|---|---|---|
| `20260831_aaa` | `2026-08-31T12:09:43+09:00` | `recording` | `session.json` のみ |
| `20260831_aaa_02` | `2026-08-31T12:09:55+09:00` | `recording` | `session.json` のみ |
| `20260831_aaa_03` | `2026-08-31T12:09:56+09:00` | `recording` | `session.json` のみ |
| `20260831_aaa_04` | `2026-08-31T12:10:05+09:00` | `recording` | `session.json` のみ |

`transcript.txt`・`transcript_segments.json`・`audio/recording.wav` は
**いずれも生成されていない**。セッション作成の直後、マイク取得で失敗したことを示す。

---

## 修正方針

### 1. デバイス解決を純関数へ

`frontend/src/features/transcription/inputDevice.ts` に
`resolveInputDevice(savedDeviceId, savedDeviceLabel, devices)` を新設する。

判定順序:

| 順 | 条件 | `matchedBy` | 使用する制約 |
|---|---|---|---|
| 1 | `audioinput` が 0 件 | — | エラーを返す（`ok: false`） |
| 2 | 保存 `deviceId` が `"default"` | `default` | `deviceId` 指定なし |
| 3 | 保存 `deviceId` が一覧に存在 | `deviceId` | その ID を `exact` 指定 |
| 4 | ID は無効だが保存ラベルと完全一致が **1 件だけ** | `label` | 現 origin の新しい ID を `exact` 指定 |
| 5 | ラベル一致なし／複数で一意に決められない | `default` | `deviceId` 指定なし（OS 既定入力） |

`groupId` は使わない。

### 2. `getUserMedia` の安全なフォールバック

一覧照合のあとにデバイスが外れる競合を考慮し、**最大 1 回だけ**再試行する。

| 例外 | 挙動 |
|---|---|
| `OverconstrainedError` / `NotFoundError` | `deviceId` 指定なしで **1 回だけ**再試行 |
| `NotAllowedError` | 再試行せず、マイク権限が拒否された旨を表示 |
| `NotReadableError` | 再試行せず、他アプリ使用中／OS から取得できない旨を表示 |
| `SecurityError` | 再試行せず、セキュリティ設定により利用できない旨を表示 |
| `AbortError` | 再試行せず、開始が中断された旨を表示 |
| その他 | `name` と `message` を使った一般エラー |

無制限の再試行は行わない。

### 3. 通知

| 状況 | 文言 |
|---|---|
| 既定入力へフォールバック | 保存された入力デバイスが見つからないため、既定の入力デバイスを使用しました。必要に応じて設定から選び直してください。 |
| ラベル一致で再解決 | 入力デバイスの識別情報を現在の環境に更新しました。 |

- 非ブロッキング表示（録音開始を妨げない）
- **既定入力へのフォールバック通知は表示から 8 秒で自動的に消す**
  （`FALLBACK_AUTO_DISMISS_MS = 8000`）。判定は文言ではなく
  `UiNotice` の `kind: 'input-device-fallback'` で行う。
  マイク権限拒否・Backend 異常・録音開始失敗など、ユーザー操作が必要な通知は
  従来どおり残す。自動消去しても録音・文字起こしと診断ログの記録には影響しない
- この通知だけは `.banner-floating` で**浮かせて**表示する。
  320x530 では 3 行のバナーが 82px を占め、開始／停止・クリア・マイGPT を
  画面外へ押し出してしまうため（実測: はみ出し 20px → 129px、ボタン下端 565 > 502）。
  浮動表示にすることで、警告表示中もはみ出し 34px・ボタン下端 470 に収まる
- 診断ログへ `matchedBy` と `fallbackReason` を記録する
- **完全な `deviceId` はログに出さない**
- 設定モーダルは、保存済み ID が一覧に無い場合に空欄のままにせず
  「既定の入力デバイス」を選択状態で表示する
- **フォールバックしただけでは保存設定を書き換えない**
  （USB 機器の一時的な取り外しを恒久化しないため）。
  ユーザーが設定画面で保存したときだけ `deviceId` / `deviceLabel` を永続化する

### 4. 設定へ `deviceLabel` を追加

`deviceId` に加えて `deviceLabel` も保存する。
旧設定に `deviceLabel` が無い場合も正常に読み込めること（後方互換）。

### 5. 空セッションの後処理

マイク取得または録音初期化に失敗した場合、作成済みセッションを
`status: "recording"` のまま残さない。

- `session.json` を `status: "failed"` へ確定し、`ended_at` を設定する
- 失敗理由は `diagnostics.log` へ記録する
- **セッションフォルダも既存ファイルも削除しない**
- 複数回呼んでも安全（冪等）
- 後処理自体の失敗で元のマイクエラーを隠さない

`status` の値は既存の `/api/session/finalize` が任意文字列を受け付けるため、
**新規 API は追加しない**。`session.json` の `status` を分岐に使っている
コードは存在しないことを確認済み（`TERMINAL_STATUSES` は File Trans の
ジョブ状態であり、セッション状態ではない）。

---

## 関連

- [`0005-input-device-misselection.md`](0005-input-device-misselection.md) —
  入力デバイスの**誤選択**に気付けない問題。本 Issue とは別（そちらは未着手）。
