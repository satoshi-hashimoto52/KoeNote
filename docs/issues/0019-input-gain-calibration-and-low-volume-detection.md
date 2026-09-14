# Issue #0019: 入力音量の自動補正・事前テスト・低音量検知

- 状態: **解決**（Backend 215 件 / Vitest 361 件で検証。#0004 を統合）
- 残課題: 最遠席・低音量環境での実測は**未確認**（下記「将来の確認事項」）
- 起点コミット: `49d533c`（`docs: sync documentation with v0.1.0 release`）
- 種別: bug（音声の大半が文字起こしされない）+ feature（入力テスト / 低音量警告）
- 重要度: **高**
- 影響: 本体マイクで遠方の会話を録音する全セッション。会議録の 4/5 が失われる

---

## 症状

MacBook 本体マイクで 61 分 46 秒の会議を録音したところ、
`recording.wav` は全区間正常に保存されていたのに、
`transcript.txt` は **4,270 字**しか生成されなかった。

体感（1 時間の会議）に対して明らかに少なく、会話の大半が欠落していた。
`diagnostics.log` には `input_device` の 2 行しか残っておらず、
**欠落したことを検知できる痕跡が一切なかった。**

---

## 確定した原因

### 主原因: 固定 `MIN_RMS` による窓の事前破棄

`backend/services/live_transcriber.py` に固定閾値がある。

```python
MIN_RMS = 0.006
...
if rms < MIN_RMS:
    return _skipped_result(model_key, rms, f"low_rms<{MIN_RMS}", ...)
```

`transcribe_pcm16()` / `transcribe_audio_chunk()` / `transcribe_wav_file()` の
3 経路すべてで、この閾値を下回る窓は **faster-whisper へ渡す前に破棄**される。

本録音は本体マイクで会議室の離れた会話を拾ったため全編が低レベルだった。

| 指標 | 実測 |
|---|---|
| 全体 RMS | 0.008167（-41.8 dBFS） |
| 10 秒窓 RMS の中央値 | **0.0039（-48.2 dBFS）** |
| 100ms フレーム RMS のノイズフロア（10%ile） | 0.00081（-61.8 dBFS） |
| 発話レベル（95%ile） | 0.01234（-38.2 dBFS） |
| 実効 SNR | **23.7 dB**（音質自体は良好） |

実際の設定（`chunk_seconds=10` / `overlap_seconds=2`）で窓分割を再現すると、

```
全 463 窓中 MIN_RMS 未満 = 366 窓（79.0%）

  0-5分  78.4%   25-30分  83.8%   50-55分  13.5%
  5-10分 100.0%  30-35分  89.2%   55-60分  27.0%
 10-15分  94.6%  35-40分  94.6%   60-62分  47.4%
 15-20分  97.3%  40-45分  97.3%
 20-25分  97.3%  45-50分  91.9%
```

`MIN_RMS = 0.006` はノイズフロア 0.00081 の **7.4 倍**であり、
遠方の通常発話がそのまま閾値未満に落ちる。

### 副原因1: 欠落が診断に残らない

`backend/services/live_session.py:533`

```python
has_speech = float(result.get("rms", 0.0) or 0.0) >= MIN_RMS
...
elif not has_speech:
    # 無音の窓で確定が進まないのは正常。カーソルは通常どおり進める。
    self._no_progress_rounds = 0
    self._needs_recheck = False
```

`rms < MIN_RMS` は「正常な無音」に分類され、

- `log_degraded()` を呼ばない
- `counters.untranscribed_seconds` へ計上しない
- カーソルは通常どおり前進する

ため、**1 時間走っても異常の痕跡がゼロ**になる。

### 副原因2: `temperature=0` スカラー指定による反復ハルシネーション

`TRANSCRIBE_KWARGS` の `temperature: 0` はスカラーであり、
faster-whisper 1.2.1 の temperature fallback を無効化する。

`.venv/lib/python3.14/site-packages/faster_whisper/transcribe.py`

```python
temperature: Union[float, List[float], Tuple[float, ...]] = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
...
temperatures=(temperature if isinstance(temperature, (list, tuple)) else [temperature]),
...
for temperature in options.temperatures:      # スカラーだと 1 周で終わる
    ...
    if compression_ratio > options.compression_ratio_threshold:
        needs_fallback = True                 # 再デコードされずに終わる
```

実測: 救済処理中の 3305.62–3336.00 秒で
`compression_ratio = 51.46` / 446 字の「はい」反復が発生した。
同じ音声を窓をずらして fallback 有効で取り直すと、
実会話 185 字（「最終的にはやってほしいですけど…」以下 9 セグメント）が復元された。

**30 秒分の実会話が反復文字列に置き換わって失われていた。**

### 副原因3: `transcript_segments.json` が生成されない

`session.json` は `"segments_path": "transcript_segments.json"` を宣言するが、
`backend/services/session_store.py` は定数を定義するだけで、
このファイルを書き出すコードが存在しない。

今回の障害解析と救済ではタイムスタンプ付きセグメントが決定的に有効だったが、
実運用のセッションからは取得できない。

> Issue #0004「session.json の segments_path が指すファイルが生成されない」と同一主題。
> 本 Issue の副原因として実装するため、#0004 は本 Issue に統合して解決する。
> （#0002 / #0003 / #0005 / #0014 には本 Issue では手を付けない）

---

## 検証: 救済処理による裏付け

`recording.wav` を正規化し、`MIN_RMS` ゲートを外して全編再処理した結果。

| 指標 | 旧 `transcript.txt` | 救済後 |
|---|---|---|
| 文字数（改行除く） | 4,270 | **20,114** |
| 1 分あたり | 69.1 字 | 325.6 字 |
| 30 秒以上の無出力ギャップ | 多数 | **0 件** |

区間別の増加倍率が `MIN_RMS` スキップ率の分布と完全に一致した。

| 区間(分) | 旧(推定字) | 救済後(字) | 倍率 | スキップ率 |
|---|---|---|---|---|
| 5–10 | **0** | 1,829 | — | **100.0%** |
| 15–20 | 30 | 1,522 | 50.7x | 97.3% |
| 20–25 | **0** | 1,521 | — | 97.3% |
| 50–55 | 1,236 | 1,540 | 1.2x | **13.5%** |

原因が `MIN_RMS` であることの追認となる。

---

## 設計

### 1. 入力モードとデバイス別設定

デバイス名だけで処理を固定せず、「入力モード」と「デバイス別設定」を組み合わせる。

| モード | 用途 |
|---|---|
| `auto` | 既定。デバイス名から仮想オーディオデバイスを判定して振り分ける |
| `mic` | 物理マイク。自動音量補正あり |
| `loopback` | 内部音声（BlackHole 等）。補正なし |
| `custom` | 手動指定 |

`auto` は BlackHole / Soundflower / Loopback / VB-Cable / Existential Audio 等の
既知の仮想デバイス名に一致した場合のみ `loopback`、それ以外は `mic` とする。
判定結果は UI に表示し、ユーザーが手動で上書きできる。

デバイス別設定は `inputProfiles` に `デバイスラベル -> 設定` で保存する。
デバイスが見つからずフォールバックした場合は、
**旧デバイスの設定を引き継がず、フォールバック先のラベルでプリセットを再解決する。**

再解決は 2 箇所で行う。**どちらか一方だけでは不十分**。

| 箇所 | 関数 | 契機 |
|---|---|---|
| 設定画面でデバイスを選び直したとき | `profileForDevice()` | ユーザー操作 |
| **録音開始直前** | `resolveProfileForActualDevice()` | `getUserMedia` が実際に開いたデバイス |

設定は保存済みデバイス名で読み込むが、`getUserMedia` が実際に開くデバイスは
フォールバックで別物になりうる（USB の抜き差し、origin 変更、既定入力への後退）。
録音開始直前に実デバイス名（`track.label`）で引き直さないと、
BlackHole 用の「補正なし・絶対無音判定」が本体マイクへ適用され、
**0019 の欠落条件がそのまま再現する。**
Backend へ送る `device_label` も、保存済みではなく実デバイス名にする
（Backend の自動判定が誤ったデバイス名で行われないようにするため）。

### 2. 入力方式別プリセット（初期値の根拠）

すべて本 Issue の実音声（61 分 46 秒）での実測に基づく。

| 定数 | 値 | 根拠 |
|---|---|---|
| `TARGET_SPEECH_DBFS` | **-24.0** | 救済処理でこの目標値により 20,114 字を得た |
| `MAX_GAIN_DB` | **24.0** | 救済に必要だった実測値は最大 +19.9 dB。余裕を持たせつつ、ノイズフロア -61.8 dBFS が -37.8 dBFS に留まる上限 |
| `SPEECH_OVER_FLOOR_DB` | **9.0** | この閾値で発話フレーム率 32.5%。会議音声の発話密度として妥当 |
| `ABSOLUTE_SILENCE_RMS` | **0.0002** | -74 dBFS。16bit で約 6.5 LSB 相当。実測ノイズフロアの 1/4 で、遠方発話を捨てない |
| `CEILING` | **0.95** | クリッピング防止の天井 |
| `ATTACK_SECONDS` | **0.25** | ゲインを下げる方向（音が大きくなった）は速く |
| `RELEASE_SECONDS` | **4.0** | ゲインを上げる方向はゆっくり。ポンピングを防ぐ |
| `NOISE_FLOOR_DOWN_SECONDS` | **2.0** | ノイズフロア追従（下降） |
| `NOISE_FLOOR_UP_SECONDS` | **45.0** | ノイズフロア追従（上昇）。低パーセンタイル相当になる非対称比 |
| `PEAK_HOLD_SECONDS` | **1.0** | 直近ピークでゲインを頭打ちし、クリッピングを事前に防ぐ |

プリセット:

| 項目 | mic | loopback | custom |
|---|---|---|---|
| 音量補正 | `auto` | `none` | `auto`/`none`/`manual` |
| 手動ゲイン | — | 0 dB | 0〜24 dB |
| 無音判定 | `relative` | `absolute` | `relative`/`absolute`/`manual` |
| 最大ゲイン | 24 dB | 0 dB | 0〜24 dB |
| クリッピング防止 | ON | ON | ON（固定） |
| 低音量警告 | ON | ON | ON/OFF |
| 警告までの継続時間 | 20 秒 | 20 秒 | 5〜120 秒 |
| 音声強調（EC/NS/AGC） | OFF | OFF | OFF |

`loopback` が絶対判定のみなのは、BlackHole の無音が厳密なデジタル 0 であり、
ノイズフロアが 0 になって相対判定が成立しないため。

### 3. 無音判定と VAD の役割分担

**窓平均 RMS による判定は、相対化しても使えない。** 実測:

| 判定 | スキップ率 |
|---|---|
| 旧 `MIN_RMS 0.006`（絶対・窓平均） | 79.2% |
| 相対・窓平均 `noise_floor × 3` | 57.1% |
| 相対・窓平均 `noise_floor × 4` | **85.7%** |
| **窓内の発話フレーム数で判定** | **0.8%** |

10 秒窓の平均 RMS は発話の合間の無音を含むため、ノイズフロアとの比が小さい。
そのため判定は「窓の平均レベル」ではなく
**「窓内にノイズフロア +9 dB を超えるフレームが 1 つ以上あるか」** で行う。

役割分担:

| 層 | 役割 | 対象 |
|---|---|---|
| 絶対下限 `ABSOLUTE_SILENCE_RMS` | 無効入力・デジタル無音の検出のみ | 補正後 PCM |
| 相対フレーム判定 | 窓に発話候補があるかの判定。推論コストの節約 | 補正後 PCM |
| faster-whisper `vad_filter`（Silero） | 実際の発話区間の切り出し | 補正後 PCM |

独自ゲートは「明らかに音が無い窓を推論に回さない」ためだけに使い、
発話/非発話の実判定は Silero VAD に任せる。
補正後の PCM を VAD に渡すことで、低レベルゆえの取りこぼしを防ぐ。

### 4. 音声処理

- `recording.wav` には**補正前の生 PCM** を保存する
  （`whisper.py` の `recorder.append(audio_bytes)` は生バイトのまま）
- 補正は `LiveSession.append_pcm()` の中だけで行い、
  解析用リングバッファへ入る PCM にのみ適用する
- ゲインはサンプル数・時刻を変えない（1 サンプル 1 乗算）
- 発話が検出されたフレームでのみゲインの目標値を更新し、
  無音・環境音だけの区間ではゲインを保持する（ノイズを持ち上げない）
- 直近 1 秒のピークホールドでゲインを頭打ちし、天井 0.95 で clip する

実測（本 Issue の音声、ストリーミング実装のプロトタイプ）:

```
ノイズフロア推定 中央値 = -57.2 dBFS
ゲイン 中央値 = +19.8 dB / 最大 +23.5 dB
補正後 発話フレーム中央値 = -25.5 dBFS（目標 -24.0）
補正後 ピーク最大 = -0.45 dBFS（天井ちょうど、超過なし）
天井到達フレーム = 21 / 28,960（0.07%）
窓スキップ率 = 0.8%（旧 79.2%）
```

### 5. 入力テスト（キャリブレーション）

設定画面から起動する 3 ステップ。**通常の録音セッションは開始せず、
ユーザーの保存先にファイルを残さない。**

1. 環境音測定（5 秒）: 「話さずにお待ちください」→ ノイズフロア
2. 遠方発話測定（18 秒）: 最も遠い席から定型文を読む → 発話レベル
3. 結果表示

算出する値（すべて **dBFS**。dB SPL ではない）:

- 入力デバイス / 判定された入力モード
- ノイズフロア（フレーム RMS の 10%ile）
- 発話レベル中央値（発話フレームの中央値）
- 小さい発話側の代表値（発話フレームの 20%ile）
- 発話ピーク（|x| の最大）
- SNR（発話レベル中央値 − ノイズフロア）
- 現在設定での推定通過率 / 推定スキップ率
- 推奨ゲイン
- クリッピングリスク
- 総合判定

最大値・最小値だけでは判定しない。中央値・パーセンタイル・継続時間を用いる。

総合判定:

| 判定 | 条件 |
|---|---|
| `no_input` 入力を検出できない | 発話フレーム 0 かつピークが絶対下限未満 |
| `clipping` 音割れの可能性 | クリップ率 > 0.1% |
| `noisy` 環境音が大きすぎる | SNR < 10 dB |
| `too_quiet` 入力が小さすぎる | 推奨ゲイン > `MAX_GAIN_DB` |
| `needs_adjust` 要調整 | 補正後推定通過率 < 80% または SNR < 15 dB |
| `usable` 使用可能 | 通過率 ≥ 80% かつ推奨ゲイン > 12 dB |
| `good` 良好 | 通過率 ≥ 95% かつ SNR ≥ 15 dB かつ推奨ゲイン ≤ 12 dB |

本 Issue の音声は SNR 23.7 dB / 推奨ゲイン約 +20 dB / 補正後通過率 99.2% のため
**`usable`（使用可能）** と判定される。補正で救えるが、
マイクを近づければより良くなる、という正しい評価になる。

### 6. 低音量警告

以下を区別し、設定時間（既定 20 秒）継続したときだけ 1 回警告する。

| 状態 | 警告 | 説明 |
|---|---|---|
| `ok` | しない | 正常 |
| `silent_ok` | **しない** | 発話が無く本当に静か（通常の無言時間） |
| `no_input` | する | 完全に入力がない |
| `too_quiet` | する | 音量が小さい（最大ゲインでも目標に届かない） |
| `low_snr` | する | 環境音が大きすぎる |
| `clipping` | する | 音割れしている |

- Backend / 文字起こし停止は**既存の無進捗ウォッチドッグ**（`watchdog.ts`）が担当し、
  本警告は一切関与しない（誤検知の相互汚染を避ける）
- 一度警告したら状態が `ok` へ戻るまで再通知しない（連打防止）
- 既存 `uiNotice` の枠組みに `low-input-level` kind を追加する

### 7. 診断記録

チャンクごとに記録せず、**60 秒ごとの集計**と**状態変化時**のみ記録する。

```
[timestamp] input_profile device=... mode=mic(auto) preset=mic warn=on
[timestamp] input_levels window=60s noise_floor_dbfs=-57.2 raw_rms_dbfs=-48.1
            corrected_rms_dbfs=-28.9 gain_db=19.8 peak_dbfs=-3.2 limited_frames=12
            level_skipped_seconds=4.0 level_skipped_ratio=0.008 vad_silence_seconds=18.4
[timestamp] input_level_state from=ok to=too_quiet
[timestamp] calibration_applied at=... verdict=usable recommended_gain_db=20.1
```

`untranscribed_seconds` との意味の重複を整理する。

| カウンタ | 意味 |
|---|---|
| `level_skipped_seconds` | レベル判定で推論へ回さなかった秒数（**新設**） |
| `vad_silence_seconds` | 推論はしたが Silero VAD が発話なしとした秒数（**新設**） |
| `untranscribed_seconds` | 推論もしたが whisper が文字化せず、再試行も尽きた秒数（**従来どおり**） |

`level_skipped_seconds` は「正常な無音」であっても必ず計上する。
旧実装のように完全に隠さない。

### 8. temperature fallback

`temperature: 0` → `temperature: (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)`（tuple）。

faster-whisper 1.2.1 の型注釈 `Union[float, List[float], Tuple[float, ...]]` で
正式に受け付ける形式であることをインストール済みパッケージで確認済み。

ライブ経路と再処理経路で同一の `TRANSCRIBE_KWARGS` を使い続ける。

異常反復の扱い:

1. `compression_ratio > 2.4` または n-gram 反復検出でセグメントを異常と判定
2. fallback は faster-whisper 内部で自動的に試行される
3. それでも異常なセグメントは**確定テキストへ保存しない**（破棄）
4. 破棄は `DegradeReason.REPETITIVE_SEGMENT_DROPPED` として診断へ記録する

### 9. transcript_segments.json

宣言どおり実際に書き出す。長時間録音でメモリを圧迫しないよう、
確定のたびに **1 行 1 JSON（JSON Lines）で逐次追記**し、
セッション確定時に配列形式の JSON へまとめ直す。

保存する項目:

| キー | 内容 |
|---|---|
| `start` / `end` | セッション基準の絶対経過秒 |
| `text` | 発話テキスト |
| `state` | `committed`（確定）/ `partial` は保存しない |
| `avg_logprob` / `no_speech_prob` / `compression_ratio` | 認識品質指標 |

---

## 追加・変更する設定キー

**追加は 10 件。既存キーの削除・変更は無い。**

| キー | 型 | 既定値 | 備考 |
|---|---|---|---|
| `inputMode` | string | `'auto'` | `auto` / `mic` / `loopback` / `custom` |
| `inputProfiles` | object | `{}` | デバイスラベル -> 設定の保存 |
| `gainMode` | string | `'auto'` | `auto` / `none` / `manual` |
| `manualGainDb` | number | `0` | `gainMode='manual'` のとき使用。0〜24 |
| `maxGainDb` | number | `24` | 自動補正の上限。0〜24 |
| `silenceMode` | string | `'relative'` | `relative`（ノイズフロア基準）/ `absolute`（絶対下限のみ）/ `manual` |
| `manualSilenceRms` | number | `0.0002` | `silenceMode='manual'` のとき使用。0〜0.05 |
| `lowInputWarning` | boolean | `true` | 低音量警告の ON/OFF |
| `lowInputWarningSeconds` | number | `20` | 警告までの継続時間。5〜120 |
| `showAdvancedAudio` | boolean | `false` | 詳細設定セクションの開閉状態 |

移行方針:

- 既存キーは一切削除・変更しない
- 新キーが無ければ既定値を補う
- 不正値は安全な既定値へフォールバック（clamp）
- `inputProfiles` に該当デバイスが無ければ既定プリセットとして扱う
- 設定ファイル破損時の既存挙動（空オブジェクトを返す）を維持する

---

## 実装計画

| # | 対象 | 内容 |
|---|---|---|
| 1 | `backend/services/audio_levels.py`（新規） | dBFS 変換 / ノイズフロア追従 / オフライン統計 / 判定 |
| 2 | `backend/services/input_profile.py`（新規） | 入力モード / プリセット / 自動判定 / 正規化 |
| 3 | `backend/services/input_gain.py`（新規） | ストリーミング適応ゲイン + リミッタ |
| 4 | `backend/routes/audio.py`（新規） | `/api/audio/analyze`（キャリブレーション） |
| 5 | `backend/services/live_transcriber.py` | `MIN_RMS` 撤去 / temperature tuple / 反復検出 |
| 6 | `backend/services/live_session.py` | `append_pcm` で補正 / フレーム基準ゲート / 集計 |
| 7 | `backend/services/session_store.py` | `transcript_segments.json` の逐次保存 |
| 8 | `backend/routes/whisper.py` | プロファイル受け渡し / 生 PCM を recorder へ / 集計診断 |
| 9 | `backend/services/word_commit.py` | 新カウンタ・新 reason コード |
| 10 | `frontend/src/features/audio/*`（新規） | 入力モード / レベルメーター / 警告 / キャリブレーション |
| 11 | `frontend/src/components/*` | 設定画面（基本 / 詳細）/ 入力テスト / レベルメーター |
| 12 | `electron/ipc/settingsMigration.ts` | 新キーの移行対象追加 |
| 12b | `electron/ipc/atomicJson.ts`（新規） | 設定の同時書き込みで ENOENT になる既存バグの修正（0019 で保存契機が増え顕在化） |
| 13 | docs / README | 全面更新 |

---

## テスト方針

実音声そのものはリポジトリへ追加しない。
**特徴（ノイズフロア -62 dBFS / 発話 -38 dBFS / SNR 24 dB）を再現した
小さな合成 fixture** を生成して回帰テストにする。

実データのコピー・コミット・fixture 化・発話内容のログ記録は行わない。

---

## 実機検証（2026-09-14）

開発版で 55.5 分の実会議を録音した結果。

| 指標 | 旧（2026-09-08） | 今回（2026-09-14） |
|---|---|---|
| 録音長 | 61.8 分 | 55.5 分 |
| 文字数（改行除く） | 4,270 字 | **14,996 字** |
| 1 分あたり | 69.1 字 | **270.3 字** |
| レベル起因のスキップ | 79.0% | **0.0 秒 / 0.0%** |
| 反復ハルシネーション | 1 件（446 字） | **0 件** |
| `transcript_segments.json` | 生成されず | **408 セグメント** |
| 30 秒以上の無出力ギャップ | 多数 | 1 件 |

診断ログ（60 秒集計 × 51 窓）から確認できた挙動:

- 入力モードは `Default - MacBook Airのマイク (Built-in)` を `mic` と自動判定
- ゲインは **+0.0〜+18.8 dB** の範囲で変動（中央値 +6.8 dB、上限 24 dB には未到達）。
  固定値ではなく入力へ追従している
- 生 RMS 中央値 -33.3 dBFS → 補正後 -26.4 dBFS（目標 -24.0 dBFS）
- 補正後のクリップ **0 サンプル**。リミッタ介入 23 フレーム、補正後ピーク -0.4 dBFS（天井 -0.45）
- `low_snr` へ 3 回・`silent_ok` へ 4 回遷移したが、**いずれも 8〜24 秒で復帰**しており
  警告しきい値 20 秒を超えたものは無い（＝警告は 1 度も出ていない）。
  一時的なノイズや無言で警告が飛ばないという意図どおりの挙動
- `recording.wav` の実測 RMS は **-33.1 dBFS** で、診断ログの `raw_rms -33.3` 側に一致
  （`corrected_rms -26.4` ではない）。**原本が無補正で保護されている**ことを実機で確認

### この検証で確認できていないこと

今回の録音は**入力レベル自体が高かった**。
1 分窓 RMS の中央値は -33.2 dBFS で、0019 の発端となった旧録音（-48 dBFS）より **15 dB 高い**。
旧 `MIN_RMS 0.006`（-44.4 dBFS）を下回った区間は 55 分中 **3 分**しかない。

つまり今回の改善は「補正が効いた」効果と
「マイクに近い良い録音条件だった」効果の**両方**を含んでおり、
**本 Issue が本来の対象とする遠方・低音量条件での効果は分離できていない。**

---

## 将来の確認事項

### 1. 最遠席・低音量環境での実測（未確認）

0019 が対象とする条件そのものでの検証が残っている。

再現条件:

- MacBook を実際の会議での設置位置へ置く
- 話者は**会議室で最も遠い席**に着く
- 通常の会話音量で話す
- 目安として 1 分窓 RMS が **-45 dBFS 前後**（旧録音は -48 dBFS）になる配置

確認したいこと:

| 項目 | 期待 |
|---|---|
| `level_skipped_ratio` | 0 に近いこと（旧実装なら 79%） |
| `gain_db` | +15〜+24 dB 程度まで上がること（今回は最大 +18.8 dB 止まり） |
| 低音量警告 | 補正しても届かない場合に `too_quiet` が 20 秒継続して発火すること |
| 入力テストの総合判定 | `usable` または `要調整` になり、推奨ゲインが提示されること |
| 文字数密度 | 200 字/分以上を維持すること |

この条件を満たす録音が取れるまで、
**遠方・低音量での効果は「設計上そうなるはず」であって「実測で確認済み」ではない。**

### 2. 無出力区間 27.3〜28.4 分（原因未確定・参考情報）

2026-09-14 の録音で、30 秒以上の無出力ギャップが 1 件（68 秒）あった。

**本 Issue の不具合とは断定していない。** 次のいずれかで、切り分けができていない。

- 実際に発言が無い時間だった（正常）
- 発言はあったが文字化されなかった（要調査）

判断材料が無い理由は、該当時間帯に何が起きていたかの記録が無いため。
同様の区間が繰り返し観測されるようなら、その時間帯の
`recording.wav` のレベルと `diagnostics.log` の該当行を突き合わせて切り分ける。

なお `level_skipped_seconds` は全期間で 0.0 秒、`vad_silence_seconds` も 0.0 秒であり、
**レベル判定でも VAD でも「捨てた」記録は無い**。
