# Issue #0011: transcription_stalled の疑わしい発火を調査する

- 状態: **解決**（原因確定・修正・回帰テスト済み。未コミット）
- 起点コミット: `ca0997b`
- 種別: bug（誤検知）
- 影響: 誤検知なら、正常な録音中に警告音・OS 通知・ポップアップが出て録音が中断されうる
- 発見経緯: 2026-08-28 の C-3 / E-2 試験中に別セッションで観測
- 関連: 受け入れ試験 B-1（同じウォッチドッグの判定）

## 観測事実

セッション `20260828_20260828_02`。

```
started_at: 2026-08-28T09:41:33+09:00
ended_at:   2026-08-28T09:42:46+09:00   status: done
```

`diagnostics.log`

```
[2026-08-28T09:42:35+09:00] [中断] 9:42:35 文字起こしが異常終了しました
  (reason=transcription_stalled) 60 秒間 文字起こしが進んでいません
```

| 事実 | 値 |
| --- | --- |
| 発火までの経過 | 録音開始から **約 62 秒**（閾値 60 秒とほぼ一致） |
| `transcript.txt` | `[中断]` 行の **前後どちらにも文字起こしテキストが存在する** |
| wav | 66.56 秒（16,000 Hz / 1ch / Int16） |

**文字起こしが進んでいたにもかかわらず「60 秒間進んでいません」と判定された可能性がある。**
実際の停止だったのか誤検知だったのかは、この記録だけでは確定できない。

## 調査対象

判定は `frontend/src/features/transcription/useLiveTranscription.ts:332-336`。

```ts
const stalledFor = now - lastProcessedRef.current.at;
if (lastProcessedRef.current.at > 0 && stalledFor > TRANSCRIPTION_STALL_MS) { ... }
```

`lastProcessedRef` の更新は同ファイル `:432-436`。

```ts
const processed = Number(data.processed_audio_seconds ?? 0);
if (processed > lastProcessedRef.current.value) {
  lastProcessedRef.current = { value: processed, at: Date.now() };
} else if (lastProcessedRef.current.at === 0) {
  lastProcessedRef.current = { value: processed, at: Date.now() };
}
```

**`at` は `processed` が増えたときだけ更新される。**

### 仮説A：セッション跨ぎで値が持ち越される

`reconnect()`（`:712`）は値を持ち越して時刻だけ更新する。

```ts
lastProcessedRef.current = { value: lastProcessedRef.current.value, at: Date.now() };
```

Backend が再起動すると**新しい LiveSession になり `processed_audio_seconds` は 0 から始まる**。
持ち越した `value`（前セッションの大きな値）を新セッションの小さな `processed` が超えられず、
`at` が更新されないまま 60 秒経過して発火する、という筋道が成立しうる。

`start()`（`:570`）では `{ value: 0, at: 0 }` にリセットされるため、
**通常の新規録音では起きない。再接続・Backend 再起動を経た場合に限る。**

本件のセッションは Backend 再起動（09:41:03）の直後に開始されており、
再接続経路を通ったかどうかの確認が必要。

### 仮説B：実際に推論が 60 秒進まなかった

Backend 再起動直後でモデルのロードが走り、推論が遅れた可能性。
`[中断]` 行の後にテキストが続いているため、遅れて追いついたとも読める。

## 追跡すべき値の経路

| 値 | 生成 | 伝達 | 消費 |
| --- | --- | --- | --- |
| `processed_samples` | `live_session.py:610`（`_process_window`。確定テキストの有無に依存しない） | — | — |
| `processed_audio_seconds` | `live_session.py:611` | `progress_snapshot`（`:268-274`）→ heartbeat | Renderer `:431` |
| `last_transcription_at` | `live_session.py:612` | `progress_snapshot` | UI 表示 |
| `lastProcessedRef` | Renderer `:432-436` / `:570` / `:712` | — | ウォッチドッグ `:332-336` |

**セッションが切り替わったときに `lastProcessedRef` をどう扱うべきかが論点。**

## 確認方法

1. Backend を `kill -9` → 再起動 → 再接続し、**60 秒以上録音を継続**して発火するか
2. 発火した場合、診断ログの「処理済み」が実際に増えているかを同時に目視
3. 通常の新規録音（再接続なし）では発火しないことを対照確認

## 修正候補（原因確定後）

- 新しい `session_id` を受け取ったら `lastProcessedRef` を `{ value: 0, at: Date.now() }` にリセットする
- `processed` が減少した場合（＝セッションが変わった場合）も `at` を更新する
- 判定を「値の増加」ではなく「heartbeat の受信」と「値の非減少」で行う

## B-1 への影響

B-1（90 秒無音で誤検知しない）は 2026-08-26 に合格している。
本件は**再接続を経た場合の別経路**である可能性が高く、B-1 の合格は取り消さない。
ただし同じウォッチドッグの判定であるため、原因確定後に B-1 の前提を再確認すること。

---

## 原因の確定（2026-08-28）

判定ロジックを純関数 `frontend/src/features/transcription/watchdog.ts` へ抽出し、
**修正前の実装をそのまま写した legacy 版**でシナリオを時系列で流して再現した。

### 再現できた時系列（legacy 版でのテスト結果）

| シナリオ | legacy の結果 |
| --- | --- |
| 通常録音（processed が増加） | 発火しない（正常） |
| 90 秒無音（processed は増加） | 発火しない（正常。B-1 の前提） |
| processed が 60 秒動かない | 62 秒で 1 回発火（正常） |
| **Backend 再接続で processed が 0 へ戻る** | **81 秒で誤発火** |
| **開始直後 65 秒間 processed が 0 のまま** | **62 秒で誤発火** |

### 根本原因

`at`（最後に進捗を確認できた時刻）が **`processed` が厳密に増えたときしか更新されない**。

```ts
if (processed > lastProcessedRef.current.value) { at = now }
else if (lastProcessedRef.current.at === 0)     { at = now }
```

この更新規則は次の 2 つの「進んでいないが異常ではない」状態を区別できない。

1. **セッション切替**（Backend 再起動）で `processed_audio_seconds` が 0 から数え直しになる。
   旧セッションの大きな `value` を新セッションの小さな値が超えられず、`at` が凍結する
2. **録音開始直後**、Whisper モデルのロードと最初の窓の推論が終わるまで `processed` が 0 のまま。
   最初の heartbeat で `at` が確定した後、値が動かないので `at` が凍結する

`STARTUP_GRACE_MS`（15 秒）は起点の遅延にしか効かず、モデルロードの長さには足りない。

### 実測との一致

問題セッション `20260828_20260828_02` は
**開始 09:41:33 → 発火 09:42:35 = 62 秒**だった。
シナリオ2（開始直後に `processed` が 0 のまま）の legacy 再現値が **62,000 ms** で完全に一致する。

`_02` は Backend 再起動（09:41:03）の 30 秒後に開始された**新規録音**である。
モデルが未ロードの状態で `start()` した結果、最初の窓が出るまで `processed` が 0 のまま推移した。

### 棄却した仮説

**仮説A「`reconnect()` が古い `value` を持ち越した」は `_02` の説明としては棄却する。**
`start()`（`:570`）は `{ value: 0, at: 0 }` にリセットするため、
新規録音である `_02` には旧セッションの大きな値が持ち越されない。

ただし**仮説A の経路自体は実在するバグ**である（legacy 版のシナリオ4が 81 秒で誤発火した）。
再接続を経た場合に発生するため、あわせて修正した。

**仮説B「実際に推論が 60 秒進まなかった」は部分的に正しい。**
モデルロード中は実際に進んでいない。しかしそれは異常ではなく起動中であり、
**異常として通知したことが誤り**である。

## 修正

`watchdog.ts` に 4 つの責務を分けた純関数を置き、フックからは薄く呼ぶ。

| 関数 | 責務 |
| --- | --- |
| `beginSession(sessionId, now)` | 監視開始・セッション切替。前回の状態を持ち越さない |
| `updateProcessedMark(mark, sessionId, processed, now)` | processed 値の取り込み |
| `isTranscriptionStalled(mark, now, thresholdMs)` | 失速判定 |
| `stalledForMs(mark, now)` | 表示用の経過 |

発火済み状態の管理は既存の `anomalyRaisedRef` が担うため変更していない。

**更新規則の変更点**

- `sessionId` が変わったら基準を作り直す（仮説A の経路を塞ぐ）
- `processed` が**減った**場合も基準を作り直す（別セッションの値を受け取った可能性）
- `reconnect()` が `value` を持ち越さず `beginSession` を使う

**初回進捗までの猶予（`STARTUP_STALL_MULTIPLIER = 3`）**

まだ一度も進捗を観測していない（`value === 0`）間は、閾値を 3 倍（60 秒 → 180 秒）にする。
起動中と本当の停止を区別できないため、**検知を捨てずに猶予だけ延ばす**。
初回進捗が出ないまま止まっていれば 180 秒で発火する。

## 回帰テスト

`frontend/src/features/transcription/watchdog.test.ts`（11 件）

| テスト | 修正前 | 修正後 |
| --- | --- | --- |
| 通常録音で発火しない | ✅ | ✅ |
| 90 秒無音で発火しない | ✅ | ✅ |
| 60 秒動かなければ 1 回だけ発火 | ✅ | ✅ |
| **再接続で processed が 0 へ戻っても誤検知しない** | **❌ 81 秒で発火** | ✅ |
| **開始直後 processed が 0 のままでも誤検知しない** | **❌ 62 秒で発火** | ✅ |
| **session_id 切替で value と基準時刻をリセット** | **❌** | ✅ |
| **start が前回の監視状態を持ち越さない** | **❌** | ✅ |
| 初回進捗が出ないまま止まれば猶予後に発火 | （新規） | ✅ |
| 未観測（at=0）では発火しない | ✅ | ✅ |
| 一度発火したら毎 tick 繰り返さない | ✅ | ✅ |
| 停止後に判定を呼ばなければ追加発火しない | ✅ | ✅ |

**修正前 4 件失敗 / 6 件成功 → 修正後 11 件すべて成功。**

## B-1 への影響

B-1（90 秒無音で誤検知しない）の前提は維持されている。
無音でも `processed_audio_seconds` は進むため、専用のテストで発火しないことを確認した。
B-1 の合格は変更しない。

## 既存動作への影響

- 本当に文字起こしが止まった場合の検知は維持（閾値 60 秒、初回進捗前は 180 秒）
- Backend 窓処理・カーソル・確定処理には触れていない
- 他の 2 層のウォッチドッグ（キャプチャ停止 3 秒 / Backend 無応答 8 秒）は変更なし

## 閾値と境界（確定仕様）

境界の比較は **`>=`**。閾値ちょうどで発火する。

| 状態 | 閾値 | 発火しない | 発火する |
| --- | --- | --- | --- |
| 初回進捗前（`value === 0`） | **180,000 ms** | 179,999 ms まで | **180,000 ms 以上** |
| 初回進捗後（`value > 0`） | **60,000 ms** | 59,999 ms まで | **60,000 ms 以上** |

`session_id` の変更、および `processed` の巻き戻りでは基準を作り直す。
巻き戻り先が 0 なら初回進捗前として 180 秒猶予を再適用し、0 より大きければ 60 秒を適用する。

適用中の閾値は `stallLimitMs(mark, thresholdMs)` で取得できる。

## トレードオフ（意図的な設計判断）

初回進捗前の猶予を延ばしたことによる残余リスクを、隠さず記録する。

- **初回進捗前に実際の停止が起きた場合、`transcription_stalled` 単独の検知は最大 180 秒へ遅れる。**
  修正前は 60 秒だった
- ただし他の 2 層は変更していない。**キャプチャ停止は 3 秒、Backend 無応答は 8 秒**で別途検知される
- **残余リスクは「音声受信と heartbeat が継続しながら推論だけが停止した場合」**である。
  この組み合わせだけが 180 秒まで検知できない
- 将来的に Backend の heartbeat へ `loading` / `processing` / `stalled` などの状態を追加すれば、
  起動中と停止を区別できるため、**初回進捗前も 60 秒へ戻せる**。
  現時点では heartbeat にその情報がなく、区別する手段がないため猶予で対処している

誤検知を放置すると、正常な録音中に警告音と OS 通知が出てユーザーが停止操作をしかねない。
検知の遅れよりも誤検知の害が大きいと判断した。
