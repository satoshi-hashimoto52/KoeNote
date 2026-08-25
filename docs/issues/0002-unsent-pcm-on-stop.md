# Issue #0002: 停止時に未送信の PCM が破棄される

- 状態: 未対応
- 起点コミット: `ca0997b` (`fix: prevent transcription loss across window boundaries`)
- 種別: bug（末尾音声の取りこぼし）
- 影響: リアルタイム文字起こしの末尾テキストと `recording.wav` の末尾
- 関連: [#0003](0003-worklet-tail-frame-not-flushed.md)（同じ停止経路の別の取りこぼし）
- 発見経緯: E-3 不合格の原因調査中に発見。**今回の試験では発動していない**

> このリポジトリには git remote が設定されていないため、GitHub Issue の代わりに
> リポジトリ内の文書として管理する。

## 概要

`stop()` は停止要求を送る前に `flushPending(socket)` を呼ばない。
そのため `pendingRef` に滞留していた PCM フレームは、送信されないまま破棄される。

## 該当箇所

`frontend/src/features/transcription/useLiveTranscription.ts`

停止処理（`stop()`）の順序。

| 順 | 処理 | 行 |
| --- | --- | --- |
| 1 | `stopIntentRef = true` / `clearTimers()` / status→`stopping` | 673-675 |
| 2 | `await teardownCapture()` — AudioWorklet 停止・トラック停止 | 676 |
| 3 | `socket.send(JSON.stringify({ type: 'stop' }))` | 690 |
| 4 | `await waitForFinal` — `session_final` 待ち | 691 |
| 5 | `socket.close()` | 692 |

`pendingRef` は次の 2 経路で積まれる。

- 送信バッファ超過時（`socket.bufferedAmount > SEND_BUFFERED_LIMIT`）: 620-632
- WebSocket が OPEN でないとき（再接続中など）: 629-631

`flushPending`（218-227）はキャプチャのフレームコールバックからのみ呼ばれる。
キャプチャは手順 2 で停止するため、**手順 3 以降に `pendingRef` を掃き出す経路が存在しない。**

## 発生条件

次のいずれかの状態で停止した場合。

1. 停止直前に送信バッファが詰まっていた（`bufferedAmount > SEND_BUFFERED_LIMIT`）
2. 異常切断からの再接続中（`pendingRef` に最大 60 秒ぶん保持される設計）
3. Backend 応答停止のポップアップから「録音を終了して保存」を選んだ

再接続中の停止（2, 3）が最も影響が大きい。設計上ここには最大 60 秒ぶんの音声が滞留する。

## 影響

- `transcript.txt` の末尾が欠ける（滞留量ぶん）
- `audio/recording.wav` も欠ける（wav は Backend が受信 PCM から書くため）
- `dropped_seconds` は 0.0 のまま。**Backend 側は「そもそも届いていない」ため計上できない**
- `enqueueFrame` の上限超過破棄は `setDroppedClientSeconds` で計上・通知されるが、
  この経路の破棄は計上も通知もされない（**沈黙して失われる**）

## 今回の試験での状況

A-1（68 分）では発動していない。

| 確認項目 | 値 |
| --- | --- |
| Backend の `recorded_seconds` | 4,077.7 s |
| `recording.wav` 長 | 4,077.70 s |
| `dropped_seconds` | 0.0 s |

受信量と wav 長が一致しており、送信欠落はなかった。

## 競合の有無（調査結果）

「停止要求が最後の PCM フレームより先に Backend へ届く」競合は**発生しない**。
PCM フレームと `stop` は同一 WebSocket 上を流れ、WebSocket はフレーム順序を保証するため、
`send()` 済みの PCM は必ず `stop` より前に届く。

問題は順序ではなく、**`send()` されないまま捨てられるフレームがあること**。

## 修正候補

1. 手順 2 と 3 の間に `flushPending(socket)` を挿入する。
   ただし `bufferedAmount` 超過で `flushPending` は途中 return するため、
   掃き出し完了を待つループ（`bufferedAmount` が下がるのを待つ）と上限時間が必要。
2. 掃き出せなかった残量を `unconfirmed` の判定材料に含め、
   「停止（未確定分あり）」の理由として秒数を表示する。
3. 掃き出せなかった秒数を `setDroppedClientSeconds` に計上し、沈黙した欠落をなくす。

`teardownCapture()` より先に掃き出すのは不可（キャプチャが動いている間は新しいフレームが積まれ続ける）。
**キャプチャ停止後・stop 送信前**が正しい位置。

## 必要なテスト

- `pendingRef` に滞留がある状態で `stop()` を呼ぶと、`stop` 送信前に全フレームが `send()` される
- `bufferedAmount` が下がらない場合、上限時間で打ち切り `unconfirmed` になる
- 打ち切った秒数が計上され、UI に表示される
- 再接続中（socket が OPEN でない）に `stop()` を呼んだ場合の挙動が定義されている
- 掃き出し後に `stop` が送られる順序が保たれる（順序の回帰）
- 滞留ゼロの通常停止で挙動が変わらない（A-1 経路への非回帰）

## 注意

A-1 で 68 分の安定性を確認した長時間処理アルゴリズム（`plan_window` / `advance_cursor` /
`drain_on_stop` / word commit）には触れないこと。本 Issue はフロントの停止経路のみで閉じる。
