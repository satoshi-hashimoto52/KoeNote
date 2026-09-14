# ドキュメント生成レポート

生成日: 2026-08-31
初版の対象コミット: `43fdd31` (`build: rename and package KoeNote for macOS arm64`)
最終照合: Issue 0016 / 録音ステータス UI / 入力欄・ボタンの視認性 / ウィンドウ不透明度の
実装反映後（コミット前の作業ツリー）

## 作成したファイル

| ファイル | 行数 | 内容 |
|---|---|---|
| `docs/00_PROJECT_OVERVIEW.md` | — | 概要・課題・機能・技術・実行/ビルド/テスト |
| `docs/01_ARCHITECTURE.md` | — | 全体構成・レイヤ・データフロー・API 構成・状態管理・通信（Mermaid 3 図） |
| `docs/02_DIRECTORY_STRUCTURE.md` | — | ツリー・各フォルダと主要ファイルの役割 |
| `docs/03_TECH_STACK.md` | — | ライブラリ一覧・用途・使用箇所・バージョン |
| `docs/04_BUILD_AND_RUN.md` | — | 実在する install/run/build/package/test コマンド |
| `docs/05_CODING_CONVENTIONS.md` | — | 命名・配置・コメント・エラー処理・非同期・型 |
| `docs/06_API_REFERENCE.md` | — | HTTP 10 件 + WebSocket 1 件の仕様 |
| `docs/07_DATABASE.md` | — | DB 非存在の根拠とファイル永続化の仕様 |
| `docs/08_CONFIGURATION.md` | — | 設定ファイル・環境変数・既定値 |
| `docs/09_AI_DEVELOPMENT_GUIDE.md` | — | AI 向け作業ガイド |
| `docs/10_KNOWN_LIMITATIONS.md` | — | TODO 調査結果・未解決 Issue・技術的制約 |
| `docs/CLAUDE.md` | — | Claude Code 専用の指示書 |
| `docs/DOCUMENTATION_REPORT.md` | — | 本書 |
| `README.md` | — | 既存。現行実装へ更新して文書コミットへ含めた |

既存の `docs/issues/`、`docs/manual-acceptance-long-transcription.md`、
`docs/migration_analysis.md` は**変更していません**。

## 根拠にしたファイル

### 設定・マニフェスト

| ファイル | 用途 |
|---|---|
| `package.json` | スクリプト、依存、electron-builder 設定、バージョン、appId |
| `package-lock.json` | 解決済みバージョンの取得 |
| `tsconfig.json` | TypeScript 設定 |
| `vite.config.ts` | Renderer ビルド設定 |
| `vitest.config.ts` | テスト対象の指定 |
| `backend/requirements.txt` | Python 依存 |
| `backend/config/transcription_terms.json` | 用語補正のキー・件数 |
| `backend/packaging/koenote_backend.spec` | PyInstaller 設定 |
| `build/entitlements.mac.plist` | エンタイトルメント |
| `.gitignore` | 除外対象 |
| `README.md` | セットアップ手順・制限・署名・入力デバイス |

### Backend ソース

`main.py`, `routes/session.py`, `routes/whisper.py`,
`services/session_store.py`, `services/live_session.py`, `services/live_transcriber.py`,
`services/pcm_stream.py`, `services/wav_recorder.py`, `services/word_commit.py`,
`services/transcriber.py`, `services/live_registry.py`, `services/exporter.py`,
`services/file_utils.py`, `services/runner.py`, `packaging/koenote_backend.py`

### Electron ソース

`main.ts`, `preload.ts`, `backend.ts`, `backend-lifecycle.ts`,
`ipc/handlers.ts`, `ipc/openExternal.ts`, `ipc/diagnostics.ts`, `ipc/settingsMigration.ts`

### Frontend ソース

`index.html`, `src/App.tsx`, `src/services/api.ts`, `src/types/bridge.ts`,
`src/features/transcription/liveTypes.ts`, `src/features/transcription/useLiveTranscription.ts`,
`src/features/transcription/pcmCapture.ts`, `src/components/transcriptHeight.ts`

### その他

- `scripts/` 全 6 ファイルの先頭コメント
- `docs/issues/0001`〜`0015` の「状態」行
- `git log`（コミットメッセージ規約 25 件）
- `git ls-files`（追跡ファイル 99 件）

### 実行して確認した事項

| コマンド | 目的 | 結果 |
|---|---|---|
| `npm run typecheck` | 型検査の成否 | エラーなし |
| `npm run test:unit` | TS テスト件数 | 初版時 142 件 → 最終照合時 **235 件**通過 |
| `npm run test:backend` | Python テスト件数 | **106 件**通過（初版時から変化なし） |

## 情報不足だった項目

| 項目 | 状態 | 記載方法 |
|---|---|---|
| ライセンス | `LICENSE` ファイルなし。`package.json` に `license` フィールドなし | 「不明」と明記 |
| Python 依存のバージョン | `requirements.txt` にピンなし | 「バージョン指定なし」と明記 |
| ffmpeg のバージョン | どこにも記載なし | 「不明」と明記 |
| `scripts/live_soak.py` の説明 | ファイル先頭に shebang のみ。docstring なし | README の使用例のみ転記 |
| `scripts/measure_word_jitter.py` の説明 | 同上。README にも記載なし | 「記載なし」と明記 |
| `scripts/responsive_check.cjs` の実行方法 | README に記載なし | 先頭コメントのみ転記し、その旨を注記 |
| `remove_fillers` の仕様 | `transcription_terms.json` にキーは存在するが処理側の説明なし | キーの存在のみ記載 |
| CI / CD | `.github/` なし | 「存在しない」と明記 |
| Docker | Dockerfile / compose なし | 「存在しない」と明記 |
| Lint / Format | 設定ファイル・スクリプトなし | 「存在しない」と明記 |
| Feature Flag | 該当する仕組みなし | 「存在しない」と明記 |
| PR / Issue テンプレート | `.github/` なし | 「存在しない」と明記 |
| Windows / Linux ビルド | `build.mac` のみ | 「確認できない」と明記 |

## 推測せず省略した項目

| 項目 | 省略理由 |
|---|---|
| 各ライブラリの一般的な説明 | プロジェクト内の使用箇所のみ記載し、一般論は書かなかった |
| パフォーマンス特性の評価 | 実測値はリポジトリ内の記録（受け入れ試験）にしかないため、コードからの推定は書かなかった |
| セキュリティ脅威の網羅的評価 | コードに実装されている対策（CSP・許可ホスト・`contextIsolation` 等）のみ記載した |
| アーキテクチャの「あるべき姿」や改善提案 | 事実の記述に限定した |
| `services/exporter.py` / `file_utils.py` / `runner.py` の詳細仕様 | 関数シグネチャのみ確認。内部仕様は未読のため役割の一行記述にとどめた |
| File Trans の `decode_mode`（`speed` / `accuracy`）の挙動差 | 分岐は確認したが、精度・速度の差を示す記述がリポジトリ内にないため書かなかった |
| `remove_fillers` の動作 | 処理コードを特定できなかった |
| テスト件数の README 記載との差異の原因 | 下記「発見した不整合」に事実のみ記載した |

## 発見した不整合（未修正）

ドキュメント生成の過程で、`README.md` の記述と現行コードの不一致を 2 件確認しました。
**本タスクの範囲外のため修正していません。**

| 箇所 | README の記載 | 現行の事実 | 根拠 |
|---|---|---|---|
| `README.md:92` | `uvicorn main:app`, `127.0.0.1:8000` | ポートは `8765` | `electron/backend.ts:9`, `electron/preload.ts:5-6` |
| `README.md:266` | Backend ユニットテスト **53 件** | **106 件** | `npm run test:backend` の実行結果 |

`README.md:92` はポート変更時（コミット `43fdd31`）に追従漏れしたものです。
`README.md:266` はテスト追加に伴う件数のずれです。

## 今後追加すると良いドキュメント

| ドキュメント | 理由 |
|---|---|
| `LICENSE` | 公開リポジトリだがライセンスが未定義。第三者が利用条件を判断できない |
| ルートの `CLAUDE.md` | Claude Code はリポジトリルートの `CLAUDE.md` を自動読み込みする。本書は指示どおり `docs/` に置いたため自動読み込みされない |
| `CONTRIBUTING.md` | 外部からの PR 手順・確認事項が未定義 |
| `.github/workflows/*.yml` | CI がないため、typecheck / test の実行が各人の手作業に依存している |
| `.github/PULL_REQUEST_TEMPLATE.md` | チェックリストの共有 |
| `backend/requirements.txt` のバージョンピン | 再現可能なインストールのため。PyInstaller も依存として未宣言 |
| ESLint / Prettier 設定 | 現状は規約が暗黙知。`tsc --noEmit` のみが自動検査 |
| トラブルシューティング集 | `ffmpeg_ok: false`、ポート衝突、Gatekeeper 警告、モデル未取得など、README に散在する対処を一箇所へ |
| `scripts/` の README | `live_soak.py` と `measure_word_jitter.py` に説明がない |
| データ保持ポリシー | 録音・文字起こしの保存場所と削除方針が未文書化 |

## 検証

本ドキュメント群の作成にあたり、リポジトリのファイルは
**`docs/` への新規追加のみ**行いました。既存ファイルの変更はありません。

```text
docs/00_PROJECT_OVERVIEW.md
docs/01_ARCHITECTURE.md
docs/02_DIRECTORY_STRUCTURE.md
docs/03_TECH_STACK.md
docs/04_BUILD_AND_RUN.md
docs/05_CODING_CONVENTIONS.md
docs/06_API_REFERENCE.md
docs/07_DATABASE.md
docs/08_CONFIGURATION.md
docs/09_AI_DEVELOPMENT_GUIDE.md
docs/10_KNOWN_LIMITATIONS.md
docs/CLAUDE.md
docs/DOCUMENTATION_REPORT.md
```

---

## 実装との照合結果（最終）

各文書を現行の実装と突き合わせた結果です。実測は `npm run test:unit` /
`npm run test:backend` / `git ls-files` / ソースの `grep` によります。

### 共通項目

| 確認項目 | 結果 |
|---|---|
| 製品名が `KoeNote` | ✅ 13 文書中 11 文書で言及。残る 2 文書（`01_ARCHITECTURE.md` / `03_TECH_STACK.md`）は製品名に依存しない内容で、旧名称も含まない |
| bundle identifier `com.hashimoto.koenote` | ✅ 3 文書（00 / 08 / CLAUDE）で一致 |
| Backend port `8765` | ✅ 7 文書。旧 `8000` の記載なし（`8000` の一致は `RECONNECT_BACKOFF_MS` / `HEARTBEAT_TIMEOUT_MS` / `sample_rate` の下限 / `FALLBACK_AUTO_DISMISS_MS` のみ） |
| ローカルディレクトリが `KoeNote` | ✅ `02_DIRECTORY_STRUCTURE.md` のツリー冒頭 |
| `BridgeLog` 表記 | ✅ 10 箇所すべてが**設定移行の説明**または**過去記録を維持せよという指示**。現行の製品名として使っている箇所はゼロ |
| パッケージ版が arm64 | ✅ 8 文書 |
| ffmpeg / ffprobe の前提（同梱しない） | ✅ 8 文書 |
| Whisper モデルの初回取得 | ✅ 00 / 03 / 10 |
| File Trans の制約（パッケージ版で不可） | ✅ 8 文書 |
| Issue 0016 の入力デバイス制約 | ✅ 07 / 08 / 09 / 10 / CLAUDE |
| 不透明度設定 | ✅ 00 / 07 / 08 / 09 / CLAUDE |
| ユーザー固有の絶対パス | ✅ なし（`09` の `/Users/<name>/...` はチェックリストの記法） |
| 機密情報 | ✅ なし |
| 存在しないファイル参照 | ✅ なし（文書が挙げるソースパスを全件 `test -e` で確認） |
| 存在しない API / 機能 | ✅ なし（エンドポイントは実装と同数: HTTP 10 + WebSocket 1） |
| 文書内リンク切れ | ✅ なし（`README.md` からのリンクを含め全件を `test -f` で確認） |
| 実在しないファイル参照 | ✅ なし（文書が挙げるソースパスを全件確認） |
| `deviceLabel` / `windowOpacity` の反映 | ✅ `07` / `08` / `README.md` へ反映済み |

### 文書ごとの更新内容

| 文書 | 実装反映のために更新した点 |
|---|---|
| `00_PROJECT_OVERVIEW.md` | 主な機能へ「入力デバイスの再解決」「開始失敗セッションの確定」「ウィンドウの不透明度」を追加。テスト件数（Vitest 235 / Backend 106）を明記 |
| `01_ARCHITECTURE.md` | IPC 件数（`handle` 13 + `on` 2）、状態管理へ `ResolvedInputDevice` / `UiNotice` / 不透明度を追加。設定ファイルの書き手が main だけである旨を明記 |
| `02_DIRECTORY_STRUCTURE.md` | 追跡ファイル数を 125 件へ。新規モジュール 7 件と本ドキュメント群をツリー・一覧へ追加。Issue の範囲を 0001〜0016 へ |
| `03_TECH_STACK.md` | 変更なし（依存の追加・削除がないことを `package.json` / `package-lock.json` / `requirements.txt` で確認） |
| `04_BUILD_AND_RUN.md` | テストファイル一覧を現行の TS 15 / Python 12 へ更新し、件数を併記 |
| `05_CODING_CONVENTIONS.md` | 変更なし（命名・エラー処理・型の方針に変更なしを確認） |
| `06_API_REFERENCE.md` | 変更なし（Backend に変更がないことを確認。HTTP 10 + WS 1 が実装と一致） |
| `07_DATABASE.md` | 設定キー表へ `deviceLabel` / `windowOpacity` を追加 |
| `08_CONFIGURATION.md` | `deviceLabel` / `windowOpacity` の追加、不透明度の範囲・初期値・刻み・適用方法、入力デバイスの解決順序と「フォールバックでは恒久上書きしない」方針、移行対象キーの更新、0018 の配色変数を追加 |
| `09_AI_DEVELOPMENT_GUIDE.md` | 実装ルールへ 0016 / 0018 の方針を追加。テスト件数と fake timers の方針を明記。編集してよい場所へ本ドキュメント群を追加 |
| `10_KNOWN_LIMITATIONS.md` | Issue 0016 を解決済みへ移動し、入力デバイスの origin 依存を技術的制約として追加 |
| `CLAUDE.md` | テスト件数、IPC 件数、新規モジュール、設計思想（保存値を鵜呑みにしない／勝手に恒久設定へ書き戻さない／設定ファイルの書き手は main だけ）を追加 |

### README.md の照合

`README.md` も現行実装と照合し、**文書コミットへ含めました**。

| 項目 | 対応 |
|---|---|
| `127.0.0.1:8000` の誤記 | **解消**。`127.0.0.1:8765` へ修正し、`KOENOTE_PORT` で変更できる旨を追記 |
| 「Backend ユニットテスト（53件…）」 | **解消**。Vitest 15 ファイル / **235 件**、Backend 12 ファイル / **106 件**へ更新 |
| 設定移行キーの一覧 | `deviceLabel` / `windowOpacity` を追加（計 9 キー） |
| 追記した節 | 「ドキュメント」（`00_PROJECT_OVERVIEW.md` を入口とする一覧）、「画面（0015 / 0017 / 0018）」、「入力デバイスの安全なフォールバック（0016）」 |

README に含めていないもの（意図的）:

| 項目 | 確認 |
|---|---|
| ユーザー固有の絶対パス | ✅ なし |
| 一時ファイルのパス | ✅ なし（`--output-folder /tmp/koenote_soak` は soak の実行例のみ） |
| release 成果物の SHA-256 | ✅ なし（リリース情報側へ記載する） |
| 未確定の GitHub Release URL | ✅ なし |
| 実装されていない機能 | ✅ なし |

### Issue 文書の確認（0016 / 0017 / 0018）

| Issue | 文書 | 状態 |
|---|---|---|
| 0016 | `docs/issues/0016-stale-input-device-id-after-origin-change.md` | 解決 |
| 0017 | `docs/issues/0017-recording-status-readability.md` | 解決 |
| 0018 | `docs/issues/0018-window-opacity-and-control-visibility.md` | 解決 |

`0017` / `0018` は**他の用途で使われていない**ことを `docs/issues` の一覧で確認したうえで
新規に採番しました（既存は 0001〜0016）。
一般文書からの `0017` / `0018` への言及も、すべて上記の分類と一致しています。

これら 3 件はコード変更の根拠なので、**文書コミットではなくコード側のコミット**に含めています。

### 未解決として残す項目

| 項目 | 状態 |
|---|---|
| `LICENSE` の不在 | 未対応。公開リポジトリだがライセンス未定義 |
| ルート直下の `CLAUDE.md` | 未対応。Claude Code が自動読み込みするのはルートの `CLAUDE.md`。本書は指示どおり `docs/` に置いている |

---

## v0.1.0 リリース後の最終文書監査

現行の HEAD・実行アプリ・GitHub Release と、リポジトリ内の全 Markdown（**34 件**）を照合しました。
文書を先に信用せず、コードと設定から事実を確定したうえで突き合わせています。

### 実装から確定した事実

| 項目 | 値 | 取得元 |
|---|---|---|
| 製品名 / version | KoeNote / 0.1.0 | `package.json` |
| bundle identifier | `com.hashimoto.koenote` | `package.json` `build.appId` |
| 対応環境 | macOS / Apple Silicon（arm64） | `build.mac.target` |
| 初期サイズ / 最小サイズ | 320×530 / 320×480 | `electron/main.ts` |
| Backend port | 8765 | `electron/backend.ts` |
| health の `app` | `KoeNote` | `backend/main.py` |
| 設定ファイル | `~/Library/Application Support/KoeNote/koenote-settings.json` | `electron/ipc/handlers.ts` |
| 設定キー | `gptUrl` / `saveFolder` / `deviceId` / `deviceLabel` / `model` / `delayMode` / `requestTemplate` / `transcriptHeight` / `windowOpacity`（9 件） | `App.tsx` / `settingsMigration.ts` |
| 入力デバイス再解決 | `deviceId` → ラベル一意一致 → 既定入力（`groupId` は使わない） | `inputDevice.ts` |
| 警告の自動消去 | 8000ms（`input-device-fallback` のみ） | `uiNotice.ts` |
| windowOpacity | 0.70〜1.00 / 既定 1.00 / 0.05 刻み | `windowOpacity.ts` |
| ffmpeg / ffprobe | 同梱しない。`/opt/homebrew/bin` → `/usr/local/bin` を探索 | `electron/backend.ts` |
| Whisper モデル | 初回利用時に取得（tiny / base / small / medium、既定 small） | `live_transcriber.py` |
| File Trans | パッケージ版では利用不可（frozen で明示的に失敗） | `transcriber.py` |
| Vitest | 15 ファイル / **235 件** | 実行結果 |
| Backend | 12 ファイル / **106 件** | 実行結果 |
| パッケージ作成 | `npm run package:backend` → `npm run package:mac` | `package.json` |

### GitHub Release

| 項目 | 値 |
|---|---|
| 最新版 | **v0.1.0** |
| URL | https://github.com/satoshi-hashimoto52/KoeNote/releases/tag/v0.1.0 |
| 添付 | `KoeNote-0.1.0-arm64.dmg` / `KoeNote-0.1.0-arm64.zip` / `SHA256SUMS.txt` |

`README.md` へ「最新版をダウンロード」節（通常は DMG、Apple Silicon 専用、
右クリック →「開く」、`SHA256SUMS.txt` による検証、ffmpeg の前提、
Whisper モデルの初回取得、File Trans の制限）を追加しました。

公証については次の事実だけを記載しています。

- Apple Development 証明書で署名済み
- Apple による公証は未実施
- 公証には Apple Developer Program への有料登録が必要
- 現時点では対応を保留

### 文書間の整合（検索結果）

現在状態を説明する 13 文書を対象に検索しました。

| 検索語 | 結果 |
|---|---|
| `port 8000` / `127.0.0.1:8000` / `8010` | **0 件** |
| 古いテスト件数（142 / 192 / 208 / 130 / 103 / 53） | **0 件** |
| 「未公開」「未作成」「コミットしていない」「pushしていない」「未コミット」 | 現在状態を説明する箇所には **0 件** |
| 0.1.0 以外の製品バージョン | **0 件** |
| `localhost:5173` | 5 件。すべて**開発サーバの説明**または `deviceId` が origin 依存であることの説明で、正当 |
| `BridgeLog` / `bridgelog` | 現在文書に 12 件。すべて**改称履歴**または**旧設定の移行説明**で、正当 |

過去の受け入れ試験記録（`docs/manual-acceptance-long-transcription.md`）、
移植調査（`docs/migration_analysis.md`）、および各 Issue 文書の当時の記録は
**一切変更していません**。過去の証拠を現在名称へ機械的に置換していません。

### Issue 状態の一致

| 分類 | Issue |
|---|---|
| 解決済み | 0006 / 0007 / 0008 / 0009 / **0010 / 0011 / 0012 / 0013 / 0015 / 0016 / 0017 / 0018** |
| 対応中 | 0001 |
| 未解決・未着手 | **0002 / 0003 / 0004 / 0005 / 0014** |

各 Issue 文書の「状態」行と `10_KNOWN_LIMITATIONS.md` / `09_AI_DEVELOPMENT_GUIDE.md` /
`CLAUDE.md` の一覧が一致することを確認しました。

監査で見つけて修正した不一致:

| 箇所 | 内容 |
|---|---|
| `10_KNOWN_LIMITATIONS.md` | 解決済み表に **0017 / 0018 が欠落**していたので追加 |
| `issues/0011-*.md` | 状態行の「未コミット」が古い記述（実際は `50c36e4` でコミット済み）。現在状態の記述なので修正 |

> `issues/0015-*.md` の「Vitest 130 件 / Backend 103 件で検証」は、
> **当時の検証記録**として意図的に残しています（現在の件数ではありません）。

### 変更した文書

| 文書 | 変更内容 |
|---|---|
| `README.md` | ダウンロード節（Release リンク・前提・インストール・検証・署名と公証）を追加。既存の署名節を公証の事実へ統一 |
| `docs/00_PROJECT_OVERVIEW.md` | 最新リリース v0.1.0 と配布物 3 点を追記 |
| `docs/04_BUILD_AND_RUN.md` | リリース手順（添付 3 点・`SHA256SUMS.txt` の作り方・Git へコミットしないこと）を追記 |
| `docs/10_KNOWN_LIMITATIONS.md` | 解決済み Issue へ 0017 / 0018 を追加 |
| `docs/issues/0011-*.md` | 状態行の「未コミット」を実際のコミット `50c36e4` へ修正 |
| `docs/DOCUMENTATION_REPORT.md` | 本節を追記 |

コード・設定・`package-lock.json` は変更していません。

---

## Issue #0019 反映（2026-09-08）

入力音量の自動補正・入力テスト・低音量検知の実装に合わせて更新しました。
対象は `49d533c` 時点の作業ツリー（**未コミット**）。

### 変更した文書

| 文書 | 変更内容 |
|---|---|
| `README.md` | 「入力音量の自動補正と無音判定（0019）」節を新設（入力モード / プリセット / 音量補正 / 無音判定と VAD の役割分担 / 入力テスト / 低音量警告 / temperature fallback）。「トラブルシューティング（入力音量）」節を追加。入力デバイス確認節へ入力モード表示の説明を追記 |
| `docs/01_ARCHITECTURE.md` | 保存ファイル表へ `.jsonl` を追加。「音声の 2 系統（0019）」を新設し、補正が解析側にしか掛からないことと、サンプル数を変えない理由を明記 |
| `docs/02_DIRECTORY_STRUCTURE.md` | Backend 5 ファイル・Frontend 6 ファイルを追加 |
| `docs/04_BUILD_AND_RUN.md` | テスト件数を 235→345 / 106→215 へ更新。新規テストファイルを列挙。合成 fixture を使う理由（実録音を入れない）を注記 |
| `docs/06_API_REFERENCE.md` | `/api/audio/presets` `/api/audio/detect_mode` `/api/audio/analyze` を追加。`/ws/live` の `config` へ `device_label` / `input_profile`、サーバ→クライアントへ `input_level_state` と `heartbeat.input_levels` を追加 |
| `docs/07_DATABASE.md` | `transcript_segments.json` の仕様（逐次保存・復旧・レコード内容）を記述。#0004 の「未調査」記述を差し替え。「録音 WAV は無補正」を明記 |
| `docs/08_CONFIGURATION.md` | 設定キー 10 件を追加。「入力音声（0019）」節で解決順序・不正値の扱い・移行方針を記述 |
| `docs/10_KNOWN_LIMITATIONS.md` | 未解決表の 0004 を「0019 で解決」へ。0005 に緩和状況を追記。解決済み表へ 0019 を追加。「入力音量（Issue 0019）」の制約節を新設 |
| `docs/issues/0019-*.md` | 新規作成（調査結果・設計・実装計画） |
| `docs/issues/0004-*.md` | 状態を「未調査」→「**解決** — #0019 へ統合」へ。未確定事項・調査項目・必要テストへの回答を追記 |

### Issue 状態の更新

| 状態 | Issue |
|---|---|
| 解決済み | 0006〜0013 / 0015〜0018 / **0019**（**0004 を統合**） |
| 対応中 | 0001 |
| 未解決・未着手 | 0002 / 0003 / 0005 / 0014 |

> #0004（`transcript_segments.json` が生成されない）は #0019 の副原因として実装したため、
> #0019 に統合して解決扱いとしました。#0002 / #0003 / #0005 / #0014 には手を付けていません。

### 記載した主な設計値

| 項目 | 値 | 根拠 |
|---|---|---|
| 目標発話レベル | -24.0 dBFS | 救済処理でこの値により 20,114 字を得た |
| 最大ゲイン | +24.0 dB | 救済に必要だった実測値は最大 +19.9 dB |
| 発話判定 | ノイズフロア +9 dB | この閾値で発話フレーム率 32.5% |
| 絶対下限 | 0.0002（-74 dBFS） | 実測ノイズフロア 0.00081 の 1/4 |
| 警告の継続時間 | 20 秒（5〜120） | — |

コード・設定・`package-lock.json` 以外の生成物は追加していません。
実音声・文字起こし内容・一時音声はリポジトリへ入れていません。

### 追記（2026-09-08 / 報告の突き合わせ）

実装報告の記載を実装と突き合わせ、次を是正しました。

| # | 内容 | 是正 |
|---|---|---|
| 1 | 実装報告が追加設定キーを「11 件」と記載（列挙は 10 件） | **実際は 10 件**。報告の数え違いで、列挙漏れは無い。`docs/issues/0019` の見出しへ「追加は 10 件」と明記して再発を防ぐ |
| 2 | `docs/issues/0019` の設定キー表が `silenceMode` を `'auto'`（`auto`/`manual`）と記載 | 実装は `'relative'`（`relative`/`absolute`/`manual`）。Issue 文書を実装へ合わせた。`08_CONFIGURATION.md` は当初から正しい |
| 3 | `docs/issues/0019` のプリセット表の custom 行が同じ食い違い | 同上 |
| 4 | **実バグ**: 録音開始時、`getUserMedia` がフォールバックしても保存済みラベルの設定が使われる | `resolveProfileForActualDevice()` を新設し、実デバイス名で解決し直す。Backend へ送る `device_label` も実デバイス名にした。回帰テスト 7 件 |
| 5 | `10_KNOWN_LIMITATIONS.md` / `09_AI_DEVELOPMENT_GUIDE.md` の未解決表に #0004 が残っていた | 解決済み表へ移した。3 文書とも未解決は `0001 / 0002 / 0003 / 0005 / 0014` で一致 |
| 6 | 実装報告のテスト件数（Backend 17 ファイル / Vitest 345 件） | 実測は Backend **18 ファイル / 215 件**、Vitest **22 ファイル / 361 件**。`04` / `09` / `CLAUDE.md` / `issues/0019` を実測値へ訂正 |
| 7 | **既存バグ**: `settings:set` が同時に走ると `ENOENT: rename` で保存に失敗する | 一時ファイル名が固定かつ非直列だったため。`electron/ipc/atomicJson.ts` を新設（一意な一時名 + パス単位の直列化）。旧実装は 30 並列で 29 件失敗、新実装は 0 件。回帰テスト 9 件 |

### 実機検証と未確認事項（2026-09-14）

開発版で 55.5 分の実会議を録音し、`docs/issues/0019` へ実測値を記録しました
（14,996 字 / 270.3 字・分 / レベル起因のスキップ 0.0% / 反復 0 件 /
`recording.wav` が無補正であることを実測で確認）。

ただし今回の録音は入力レベルが高く（1 分窓 RMS 中央値 -33.2 dBFS）、
**0019 が対象とする遠方・低音量条件での効果は分離できていません**。
未確認事項として `docs/issues/0019`「将来の確認事項」と
`10_KNOWN_LIMITATIONS.md` に記録しました。

無出力区間 27.3〜28.4 分（68 秒）は**原因未確定の参考情報**として扱い、
本 Issue の不具合とは断定していません。

### GitHub Issue との差異

`gh issue list --state all` の結果は **0 件**（Issues 機能は有効だが未使用）。
Issue 追跡は `docs/issues/` のローカル文書のみで行っており、
GitHub 側との状態差は**存在しません**。close 対象もありません。

---

## v0.1.1 リリース準備（2026-09-14）

Issue #0019 の実装（`bc705c0`）を配布版へ反映するため、
製品バージョンを `0.1.0` → `0.1.1` へ更新しました。

### version を保持していた箇所（調査結果）

| 箇所 | 対応 |
|---|---|
| `package.json` `version` | 0.1.1 へ更新 |
| `package-lock.json` `version` / `packages[""].version` | 0.1.1 へ更新（依存パッケージの version には影響なし） |
| Electron アプリの version | `package.json` から electron-builder が読むため、個別の保持箇所は**無し** |
| Backend の version 応答 | **無し**（`/api/health` は `app` のみ返す）。追加していない |
| `session.json` の製品 version | **無し**（`app: "KoeNote"` のみ）。追加していない |
| コード内の直書き | **0 件**（`package.json` 以外に `0.1.0` の直書きは存在しなかった） |

### 変更した文書

| 文書 | 変更内容 |
|---|---|
| `CHANGELOG.md` | **新規作成**。v0.1.1 の変更履歴と未確認事項、v0.1.0 の記録 |
| `README.md` | ダウンロード先を `releases/latest` へ。配布物名を 0.1.1 へ。CHANGELOG へのリンクを追加 |
| `docs/00_PROJECT_OVERVIEW.md` | バージョン・最新リリース・配布物名 |
| `docs/04_BUILD_AND_RUN.md` | 成果物名を `<version>` 表記へ。リリース手順（タグ・Release 作成）を追記。**署名に必要な確認**を追記。v0.1.0 を「過去のリリース実績」として残す |
| `docs/09_AI_DEVELOPMENT_GUIDE.md` | バージョン |
| `docs/CLAUDE.md` | バージョン・成果物名 |
| `docs/DOCUMENTATION_REPORT.md` | 本節 |

### 維持した v0.1.0 固有の記録

機械的な一括置換は行わず、次はそのまま残しました。

- `docs/DOCUMENTATION_REPORT.md` の「v0.1.0 リリース後の最終文書監査」節
- `docs/04_BUILD_AND_RUN.md` の「過去のリリース実績」表
- `docs/issues/` 配下の当時の検証記録
- v0.1.0 のタグ・Release・添付ファイル

### 署名証明書について

`security find-identity -v -p codesigning` が **0 valid identities** を返す状態でした。
キーチェーンの Apple Development 証明書 4 枚はすべて期限切れで、
最新のものも **2026-09-01 に失効**しています（v0.1.0 のビルドはその 1 日前）。

`package.json` は `mac.identity` を指定せず自動選択のため、
このままビルドすると署名がスキップされ配布物の性質が変わります。
再発防止として `docs/04_BUILD_AND_RUN.md` へ
「ビルド前に署名 identity を確認する」手順を追記しました。
