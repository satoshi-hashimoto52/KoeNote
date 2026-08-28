# Issue #0010: Backend 異常終了時に diagnostics.log を保存できない

- 状態: 未対応
- 起点コミット: `ca0997b`
- 種別: bug（異常記録の欠落）
- 影響: 受け入れ試験 C-3 の合格条件 1 件が未達
- 発見経緯: 2026-08-28 の C-3 / E-2 正式試験
- 関連: `docs/manual-acceptance-long-transcription.md` の C-3

## 概要

Backend が異常終了したとき、`[中断]` 行は `transcript.txt` には記録されるが、
`diagnostics.log` には記録されない。**記録先が停止済みの Backend にあるため。**

## 実測（2026-08-28）

セッション `20260828_20260828`（Backend PID 36521 を `kill -9`）。

| ファイル | 結果 |
| --- | --- |
| `transcript.txt` | `[中断] 9:39:33 文字起こしが異常終了しました (reason=backend_exit) code=null signal=SIGKILL reason=killed_possibly_oom` |
| `diagnostics.log` | **生成されず** |

対照として、同日の `20260828_20260828_02` では Backend が生きた状態で
`transcription_stalled` が発火し、`diagnostics.log` が正しく生成されている。
つまり書き込み経路自体は動作しており、**Backend の死活だけが分岐点**である。

## 原因

`frontend/src/App.tsx:274-281`。異常の痕跡を 2 経路へ書く。

```ts
const line = `[中断] ${clockTime()} 文字起こしが異常終了しました (reason=${reason}) ${detail}`;
if (bridge && transcriptPath) {
  await bridge.appendTranscriptNotice(transcriptPath, line).catch(() => undefined);
}
if (sessionDir) {
  await postDiagnostics(sessionDir, line).catch(() => undefined);
}
```

| 経路 | 実体 | `backend_exit` 時 |
| --- | --- | --- |
| `appendTranscriptNotice` | Electron main のローカル I/O（IPC） | **成功する** |
| `postDiagnostics` | `POST /api/session/diagnostics`（Backend の HTTP API） | **必ず失敗する** |

さらに `.catch(() => undefined)` で例外が握り潰されるため、
失敗が UI にもログにも現れない。**無言で記録が落ちる。**

## 問題の本質

`reason=backend_exit` は「Backend が死んだ」という事象そのものである。
その記録先を Backend の API に置いている限り、**この経路は原理的に成功しない。**
`no_heartbeat`（Backend 無応答）でも同じことが起きる。

## 修正候補

1. **`diagnostics.log` への追記を Electron main のローカル I/O へ移す。**
   `appendTranscriptNotice` と同じ仕組みで `<session>/diagnostics.log` へ直接追記する
   IPC を追加する。Backend の死活に依存しなくなる。もっとも確実
2. Backend 経由の書き込みを残す場合でも、失敗を握り潰さず、
   フォールバックとしてローカル I/O を呼ぶ
3. `.catch(() => undefined)` をやめ、少なくとも診断ログ（UI）へ失敗を出す

`session_store.append_diagnostics` は Backend 側の実装なので、
Electron から書く場合は **追記フォーマットを一致させる**こと（`[ISO時刻] 本文`）。

## 必要なテスト

- `backend_exit` で `diagnostics.log` に `[中断] … (reason=backend_exit …)` が追記される
- `no_heartbeat` でも同様に追記される
- Backend が生きている異常（`transcription_stalled` 等）で二重に書かれない
- 書き込み失敗時に無言で落ちず、UI へ通知される
- `transcript.txt` と `diagnostics.log` の本文が一致する
- セッションフォルダが存在しない場合にクラッシュしない

## C-3 再試験の条件

修正後、Backend 単体の `kill -9` を再実行し、`diagnostics.log` に `[中断]` 行が
記録されることを確認する。他の C-3 期待結果は 2026-08-28 に確認済みのため、
短時間（2〜3 分の録音）で足りる。
