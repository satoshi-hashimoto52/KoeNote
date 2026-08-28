# Issue #0008: 文字起こし欄の高さを可変にする

- 状態: **再オープン → 解決**（初回実装に縮み続ける不具合。専用ハンドル方式へ作り直し。Vitest 28 件）
- 起点コミット: `50c36e4`
- 種別: enhancement
- 影響: `TranscriptView` と設定の保存キー

## 現状（調査済み）

`TranscriptView.tsx` は `textarea` ではなく `div` 構造。

```css
.transcript        { height: clamp(260px, 42vh, 520px); position: relative; }
.transcript-scroll { height: 100%; overflow-y: auto; overflow-x: hidden; }
```

自動スクロールは `useLayoutEffect` が `committed` / `partial` の変化で発火する。
高さ変更では発火しない。

## 確定要件

- 下端ドラッグで**縦方向のみ**伸縮（横は変更不可）
- 最小高さを設ける。内部スクロールを維持
- 自動スクロールを壊さない。長文更新を妨げない
- 高さを設定へ保存し次回起動時に復元
- **ドラッグ中に設定ファイルへ連続書き込みしない**（`ResizeObserver` + デバウンス）
- 小さいウィンドウでは保存値より画面内に収まることを優先
- 異常値・古い値・極端に大きい値をクランプ
- 既存設定との後方互換を維持（キーがなくても動く）

## 採用値

| 項目 | 値 |
| --- | --- |
| 保存キー | `transcriptHeight`（`bridgelog-settings.json`） |
| 初期値 | **320 px** |
| 最小値 | **180 px** |
| 最大値 | **1200 px** |
| 画面内優先の上限 | `window.innerHeight * 0.7` |
| 保存のデバウンス | 500 ms |

## 受入条件

- クランプが最小・最大・NaN・負値・文字列・未設定で正しく働く
- 画面内優先の上限が適用される
- 自動スクロールの追従判定が変わらない
- Vitest でクランプと追従判定を検証する

## 実装

| ファイル | 内容 |
| --- | --- |
| `frontend/src/components/transcriptHeight.ts`（新規） | 採用値の定数、`normalizeTranscriptHeight`、`shouldPersistHeight` |
| `frontend/src/components/TranscriptView.tsx` | `savedHeight` / `onHeightChange` を受け取り、`ResizeObserver` + 500ms デバウンスで保存 |
| `frontend/src/styles.css` | `.transcript` に `resize: vertical; overflow: hidden;`。`height: clamp(...)` は撤去し `style` で与える |
| `frontend/src/App.tsx` | 設定の読み書き（`transcriptHeight` キー） |

- 伸縮は **CSS の `resize: vertical`** で行う。横方向は変わらない
- `resize` を効かせるため `.transcript` に `overflow: hidden` を付けた。
  内部スクロールは `.transcript-scroll` が `overflow-y: auto` で担うため維持される
- 自動スクロールの `useLayoutEffect` は `committed` / `partial` 依存のまま**変更していない**。
  高さ変更時は `ResizeObserver` 内で「追従中なら末尾へ寄せ直す」だけを行う
- 保存は `ResizeObserver` → 500ms デバウンス → `setSettings`。ドラッグ中は書き込まない
- 1px 未満の変化では書き込まない（`shouldPersistHeight`）

## テスト（18 件）

採用値 1 件 / クランプ 7 件（未設定・非数値・NaN・Infinity・0 以下・最小・最大・整数化・数値文字列）/
画面内優先 5 件 / 保存抑制 2 件 / 自動スクロール追従判定 3 件。

---

## 再オープン: 手動で伸ばしても勝手に縮む（2026-08-28）

### 現象

文字起こし欄を縦に伸ばしても、時間とともに勝手に縮んでいく。
高さの再起動後の復元も効かない。

### 根本原因

**`ResizeObserver` の `contentRect.height` を保存値として書き戻していたため、
ボーダー分だけ縮み続ける正のフィードバックループが発生していた。**

- `styles.css` はグローバルに `box-sizing: border-box`
- `.transcript` は `border: 1px solid`
- したがって `style.height = 320` のとき、**`contentRect.height` は 318**（上下ボーダー 2px を除いた内容ボックス）

旧実装の流れ。

```
setHeight(320) → ResizeObserver が 318 を通知 → persist(318)
  → App の transcriptHeight = 318 → savedHeight prop → setHeight(318)
  → ResizeObserver が 316 を通知 → …
```

デバウンス 500ms ごとに 2px ずつ縮み、最終的に最小値まで落ちる。
保存値も縮んだ値で上書きされ続けるため、再起動しても復元できない。

副次的な問題として、**`ResizeObserver` はユーザー操作とレイアウト変更を区別できない。**
ウィンドウを縮めたときのクランプ結果まで「ユーザーの希望高さ」として保存されていた。

### 修正方針

**「ユーザーが希望した高さ」と「いま表示できる高さ」を分離した。**

| 値 | 更新契機 | 保存 |
| --- | --- | --- |
| `preferredHeight` | **ユーザー操作のみ**（ドラッグ確定・キーボード） | する |
| `effectiveHeight` | `clamp(preferred, MIN, availableHeight)` | **しない** |

`availableHeight = max(round(viewportHeight * 0.7), MIN)`。
ウィンドウ縮小で `effectiveHeight` が小さくなっても `preferredHeight` は変えないため、
広げれば元の希望高さへ戻る。

**CSS の `resize: vertical` と `ResizeObserver` は廃止し、専用ハンドルを実装した。**
ユーザー操作とレイアウト変更を確実に区別するため。

- `pointerdown` でドラッグ開始（`setPointerCapture`）
- `pointermove` は**表示高さだけ**更新（保存しない）
- `pointerup` で `preferredHeight` を**1 回だけ**保存
- `pointercancel` は保存せず破棄
- 横方向の移動は無視（`clientY` の差分のみ使用）
- `role="separator"` / `aria-orientation` / `aria-valuenow` / `aria-valuemin` / `aria-valuemax` / `tabIndex=0`
- `↑` `↓` キーで 24px ずつ変更
- `window` の `resize` リスナーは unmount 時に解除

デバウンスは不要になったため撤去した（ドラッグ中は保存処理自体を呼ばない）。

### 実測（ビルド済みページを本番 Chromium で計測）

**ウィンドウ縦伸縮への追従**（希望高さ 320px）

| ウィンドウ高 | 利用可能 | 表示高さ | 判定 |
| --- | --- | --- | --- |
| 900 px | 630 | 320 | OK |
| 700 px | 490 | 320 | OK |
| 500 px | 350 | 320 | OK |
| 400 px | 280 | **280** | OK（クランプ） |
| 300 px | 210 | **210** | OK（クランプ） |
| 500 px へ戻す | 350 | **320** | OK（**希望高さへ復帰**） |
| 900 px へ戻す | 630 | **320** | OK |

**ハンドルの操作性**

| ウィンドウ | ハンドル | 画面内 | role | aria-valuenow | tabIndex |
| --- | --- | --- | --- | --- | --- |
| 960×900 | 904×12 | あり | separator | 320 | 0 |
| 400×700 | 376×12 | あり | separator | 320 | 0 |
| 320×600 | 300×12 | あり | separator | 320 | 0 |
| 320×480 | 300×12 | あり | separator | 320 | 0 |

**320px 幅・480px 高でもハンドルを操作できる。** 横スクロールは発生しない。

### 変更ファイル

| ファイル | 内容 |
| --- | --- |
| `frontend/src/components/transcriptHeight.ts` | `normalizePreferredHeight`（**ウィンドウを見ない**）/ `availableHeightFor` / `computeEffectiveHeight` / `heightFromDrag` |
| `frontend/src/components/TranscriptView.tsx` | 専用ハンドル。`ResizeObserver` を廃止 |
| `frontend/src/styles.css` | `.transcript-wrap` と `.transcript-resizer` を追加。`resize: vertical` を削除 |
| `frontend/src/App.tsx` | prop 名を `preferredHeight` / `onPreferredHeightChange` へ |

### 実装上の落とし穴（テストが検出した 2 件）

- `Math.floor(360 * 0.7)` が **251**（浮動小数で 251.999…）。`Math.round` に変更した
- `heightFromDrag` で上へ大きく動かして 0 以下になったとき、
  `normalizePreferredHeight` の「不正値 → 既定値」経路に落ちて **320px へ跳ねていた**。
  ドラッグ結果は不正値ではなく範囲外なので、最小値でクランプするよう分離した

### テスト（28 件）

採用値 1 / `normalizePreferredHeight` 5（**ウィンドウ非依存の確認を含む**）/ `availableHeightFor` 3 /
**preferred と effective の分離 7**（400px 保存 → 縮小で 252px → 保存値は 400px → 再拡大で 400px → 再起動復元 → 旧不具合の再現防止）/
ドラッグ計算 4 / 保存抑制 2 / 自動スクロール 3 / **ドラッグ中は保存せず pointerup で 1 回だけ 3**
