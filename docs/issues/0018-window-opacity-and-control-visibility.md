# Issue #0018: 入力欄・ボタンの視認性改善とウィンドウ不透明度設定

- 状態: **解決**（Vitest 235 件 / Backend 106 件で検証）
- 起点コミット: `43fdd31`
- 種別: UI / 視認性 + 機能追加
- 重要度: 中
- 影響: すべての入力欄とボタン、ウィンドウ全体の見え方

---

## 症状・要望

1. ダークテーマで**入力欄の背景がアプリ背景に溶けて**、どこが入力欄か分かりにくい。
   枠線も暗く、無効状態のボタンは「存在しないように見える」ほど薄い。
2. 他のウィンドウを透かして見たい場面があるが、不透明度を変える手段がない。

---

## 修正 1: 入力欄・ボタンの視認性

### CSS 変数へ集約

同じ用途へ個別に色を直書きせず、`:root` の変数で一元管理する。

| 変数 | 値 | 用途 |
|---|---|---|
| `--field-bg` | `#202024` | 入力欄・文字起こし本文の背景（`--card #111113` より一段明るい濃色） |
| `--field-bg-hover` | `#26262b` | 入力欄の hover |
| `--field-border` | `rgba(255,255,255,0.14)` | 通常枠線 |
| `--field-border-hover` | `rgba(255,255,255,0.24)` | hover 枠線 |
| `--field-placeholder` | `#8b8b95` | placeholder（本文より弱く、かつ読める濃度） |
| `--focus-ring` | `rgba(124,58,237,0.22)` | focus リング（アクセント約 22%） |
| `--btn-neutral-bg` / `-hover` | `#26262b` / `#303036` | 中立ボタン |
| `--btn-neutral-border` / `-hover` | `rgba(255,255,255,0.18)` / `0.30` | 中立ボタンの枠線 |
| `--disabled-opacity` | `0.62` | 無効時（従来 0.35〜0.4 から引き上げ） |

対象: タイトル入力欄、設定モーダルの URL / 保存先 / 入力デバイス / モデル / 遅延モード /
不透明度、文字起こし本文領域、開始・停止 / クリア / マイGPT / 保存 / キャンセル / 選択 /
全文コピー / 保存TXTを開く / 診断ログ内のボタン / 歯車ボタン。

### focus-visible

`:focus` では `outline: none`、`:focus-visible` のときだけ
`border-color: var(--accent)` ＋ `box-shadow: 0 0 0 3px var(--focus-ring)` を出す。
**マウスクリック後に白いフォーカス枠を残さない。**

### disabled 表示

無効時は `--disabled-opacity: 0.62`。押せないことは分かるが、ラベルは読める濃度にする。
「全文コピー」「保存TXTを開く」は無効時も**地と枠を残し、文字だけ弱める**ので、
ボタンの存在は分かりつつ有効状態と誤認しない。

### 維持したもの

開始 = 紫、停止 = 赤系、マイGPT = 紫アウトライン、ボタン高 42px、
3 操作ボタンの比率（`1.3fr / 0.9fr / 1.1fr`、320px で 1 行）。
グラデーションや強い影は追加していない。

---

## 修正 2: ウィンドウ不透明度設定（windowOpacity）

### 仕様

| 項目 | 値 |
|---|---|
| 設定キー | `windowOpacity` |
| 保存値 | `0.70`〜`1.00` の number |
| **範囲** | **0.70〜1.00** |
| **初期値** | **1.00**（未設定・壊れている場合も 1.00） |
| **UI の刻み** | **5%**（range スライダー、70〜100%） |
| 表示 | 「ウィンドウの不透明度　85%」を 1 行。補足は「100% = 透過なし」 |
| 適用方法 | **`BrowserWindow.setOpacity()`** |

**CSS の `opacity` で全体を薄くする方法は使わない**（文字が読めなくなるため）。

### ライブプレビューと復元

- スライダー操作中は**保存せずに即座にウィンドウへ反映**する
- 「保存」で `windowOpacity` を既存設定と一緒に永続化する
- **キャンセル / Escape / 背景クリック / 保存失敗**では、モーダルを開いた時点の値へ戻す
- 変更がなければ設定ファイルへ書き込まない
- 録音中は設定変更を禁止する既存仕様を維持する

### 起動時の復元

main が `BrowserWindow` 生成**直後・`show()` 前**に保存値を適用する。
`show()` のあとに当てると一瞬 100% で表示されてから切り替わるため。
旧 BridgeLog 設定の移行も `createWindow` より前に実行し、
移行された `windowOpacity` を初回表示から反映する。

### IPC の入力検証

`window:setOpacity` は **number だけ**を受け取り、main 側でも必ず
`normalizeWindowOpacity` を通してから適用する。

| 入力 | 結果 |
|---|---|
| `NaN` / `Infinity` / `-Infinity` | 既定 `1.00` |
| 文字列（`"0.8"` を含む） | 既定 `1.00`（拒否） |
| `null` / `undefined` / オブジェクト / 配列 / 真偽値 | 既定 `1.00` |
| `0.69` / `0` / `-5` | `0.70` へ clamp |
| `1.01` / `42` | `1.00` へ clamp |

`setOpacity` を持たない環境や失敗時は `{ ok: false }` を返すだけで、
設定保存全体は壊さない。失敗は診断ログへ記録する（`appendAppLogNotice`）。

### 設定ファイルの書き手

**Electron main だけ**が `koenote-settings.json` を書く。
Renderer は既存の `settings:set` IPC 経由でしか触れないため、
main と renderer が競合して書き換えることはない。
main が設定ファイルを直接読めるので、Renderer の起動を待たずに
表示前へ不透明度を適用できる。

---

## 検証

### 実機（`file://` origin）

| 項目 | 結果 |
|---|---|
| 起動時の復元 | 保存値 0.8 → `getOpacity()` = **0.80**（表示前適用） |
| ライブプレビュー | スライダー 80% → 即座に **0.80** |
| キャンセル | **1.00** へ復元、設定ファイルは未変更 |
| 保存 | `windowOpacity: 0.8` を永続化、適用 0.80 |
| 再起動 | 0.8 を復元 |
| 設定モーダル（320px） | ラベルと値が同じ行、見切れなし、内部スクロールで保存/キャンセルとも押下可能 |

> `setOpacity` はウィンドウ合成レベルの効果のため `capturePage()` には写らない。
> 数値（`getOpacity()`）で確認している。

### 320x530 のレイアウト

| | 修正前 | 修正後 |
|---|---|---|
| ボタン下端 / content 下端 | 520 / 502 | **484 / 502** |
| ボタン可視 | ❌ | **✅** |

320 / 460 / 960px いずれも横スクロールなし。`responsive_check` も全 6 幅で問題なし。

### テスト

`frontend/src/components/windowOpacity.test.ts` 18 件、
`settingsDraft.test.ts` に 7 件、`settingsMigration.test.ts` に 2 件を追加。

---

## 変更ファイル

| ファイル | 内容 |
|---|---|
| `frontend/src/components/windowOpacity.ts` | 新規。範囲・正規化・パーセント変換（main と renderer で共用） |
| `frontend/src/components/windowOpacity.test.ts` | 新規 |
| `frontend/src/components/SettingsModal.tsx` | 不透明度スライダー、キャンセル / Escape / 背景クリックでの復元 |
| `frontend/src/components/settingsDraft.ts` | `CaptureSettings` へ `windowOpacity`、保存時に normalize |
| `frontend/src/App.tsx` | `windowOpacity` の読み書き、ライブプレビューの受け渡し |
| `frontend/src/types/bridge.ts` | `setWindowOpacity` |
| `frontend/src/styles.css` | 配色変数、focus-visible、disabled、スライダー、狭幅の縦詰め |
| `electron/preload.ts` | `window:setOpacity`（number のみ） |
| `electron/ipc/handlers.ts` | IPC 登録、`applyWindowOpacity`、`readSettings` / 移行の公開 |
| `electron/main.ts` | 表示前に保存値を適用、移行をウィンドウ生成前に実行 |
| `electron/backend.ts` | `appendAppLogNotice`（診断ログへの記録手段） |
| `electron/ipc/settingsMigration.ts` | 移行キーへ `windowOpacity` |

## 関連

- [`0015-compact-window-controls-and-settings.md`](0015-compact-window-controls-and-settings.md) — 320px 常用のコンパクト UI
- [`0017-recording-status-readability.md`](0017-recording-status-readability.md) — 録音ステータスの視認性
