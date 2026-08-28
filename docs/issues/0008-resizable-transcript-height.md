# Issue #0008: 文字起こし欄の高さを可変にする

- 状態: **解決**（`transcriptHeight.ts` を新設。Vitest 18 件）
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
