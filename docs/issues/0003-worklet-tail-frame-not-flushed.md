# Issue #0003: 停止時に AudioWorklet の書きかけフレームがフラッシュされない

- 状態: 未対応
- 起点コミット: `ca0997b` (`fix: prevent transcription loss across window boundaries`)
- 種別: bug（末尾音声の取りこぼし、軽微）
- 影響: `transcript.txt` と `audio/recording.wav` の末尾、最大約 128 ms
- 関連: [#0002](0002-unsent-pcm-on-stop.md)（同じ停止経路。修正時は併せて検討する）
- 発見経緯: E-3 不合格の原因調査中に発見。**今回の試験では実害を確認していない**

## 概要

`capture.stop()` は AudioWorklet を停止するとき、
プロセッサ内に溜まっている書きかけフレームを送出させない。
1 フレーム未満（最大 `FRAME_SAMPLES` = 2048 samples = 128 ms）の音声が失われる。

## 該当箇所

`frontend/src/features/transcription/pcmCapture.ts` の `stop()`（180-193 行）。

```
ctx.onstatechange = null;
track.onended = null; track.onmute = null;
source.disconnect(); node.disconnect(); mute.disconnect();
if ('port' in node) (node as AudioWorkletNode).port.onmessage = null;
await ctx.close();
```

`port.onmessage = null` を先に外し、その後 `ctx.close()` する。
worklet 側へ「残りを吐き出せ」と伝える経路がなく、
仮に吐き出しても `onmessage` が外れているため受け取れない。

`onFrame` は `FRAME_SAMPLES` が満ちたときだけ呼ばれる設計なので、
停止時点の端数は常に捨てられる。

## 影響

- 最大 128 ms。文一つが失われる長さではない
- ただし E-3（末尾回収）の判定境界に効く可能性がある。
  「言い終わった直後に停止」の運用では、最後の 1 語の末尾が 128 ms 削れると
  Whisper の語境界がずれ、末尾語の認識が変わりうる
- `dropped_seconds` には計上されない（Backend へ届かないため）

## 今回の試験での状況

A-1（68 分）では `recorded_seconds` 4,077.7 s と wav 長 4,077.70 s が一致しており、
128 ms 単位の欠落は観測できていない。E-3 再試験は合格しており、
この端数が実害を出した記録はない。**理論上の欠落であり、実測での再現は未達。**

## 修正候補

1. worklet プロセッサへ `port.postMessage({type:'flush'})` を送り、
   端数を 1 フレームとして送出させてから `onmessage` を外す
2. 端数をゼロ埋めせず、実サンプル数だけを送る（ゼロ埋めすると無音が混入する）
3. 送出完了を待ってから `ctx.close()` する

`ScriptProcessorNode` フォールバック経路にも同じ端数がある。両方を揃えること。

## 必要なテスト

- 停止時に端数フレームが 1 回だけ送出される
- 送出されるサンプル数が実データ長と一致する（ゼロ埋めがない）
- 端数がゼロサンプルのときは何も送らない
- `flush` 後に `onmessage` が外れ、二重送出がない
- `scriptprocessor` フォールバックでも同じ挙動になる
- [#0002](0002-unsent-pcm-on-stop.md) の掃き出しより前に端数が `pendingRef` へ入る順序

## 注意

Backend 側の処理には手を入れない。フロントのキャプチャ停止経路のみで閉じる。
