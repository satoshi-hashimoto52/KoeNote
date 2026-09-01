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
