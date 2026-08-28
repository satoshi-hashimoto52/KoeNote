# Issue #0013: アプリ終了時に修復済み WAV ヘッダが巻き戻り、末尾が再生できなくなる

- 状態: 修正済み（未コミット）。防御層 + recorder ライフサイクルの両方を実装
- 起点コミット: `ca0997b`
- 種別: **bug（データ破損）**
- **重要度: 高**。録音データの末尾が通常のプレイヤーで再生できなくなる
- 影響: `audio/recording.wav`。再接続や Backend 再起動を経たセッションすべて
- 発見経緯: 2026-08-28、C-3 / E-2 再試験の前にアプリを通常終了した直後
- 関連: [#0012](0012-duplicate-backend-restart.md)（再起動の二重実行が本件の発生条件を作る）

## 事象

対象セッション `/Users/hashimoto/Desktop/20260828_20260828`

**アプリ終了前**

| 項目 | 値 |
| --- | --- |
| file size | 7,675,948 bytes |
| `afinfo` | **239.872 秒** |
| RIFF / data サイズ | 実ファイル長と一致 |

**アプリ終了後（通常終了。`kill -9` 不使用）**

| 項目 | 値 |
| --- | --- |
| file size | 7,675,948 bytes（**変化なし**） |
| `afinfo` | **236.544 秒**（**3.328 秒 減少**） |
| RIFF chunk size | 7,569,444（正しくは 7,675,940） |
| data chunk size | 7,569,408（正しくは 7,675,904） |
| 実データ | 7,675,904 bytes |
| 宣言外データ | **106,496 bytes = 3.328 秒** |
| mtime | 2026-08-28 10:37:05（終了処理の瞬間） |

**ファイルサイズが変わらずヘッダだけが縮んだ。** 音声データは物理的に残っているが、
data チャンクが短く宣言されているため、通常のプレイヤーでは末尾 3.328 秒が再生されない。

終了直前には、**終了済みセッションにもかかわらず** Backend PID 21199 が
ファイルハンドルを保持していた。

```
Python 21199  14w  REG  4876      .../transcript.txt
Python 21199  16u  REG  7675948   .../audio/recording.wav
```

`session.json` は `status: done` / `ended_at: 2026-08-28T09:41:14+09:00` であり、
セッションは 09:41:14 に正常終了していた。終了処理は graceful
（`main.ts:180-186` の `will-quit` が `stopBackend()` を待つ）である。

## 根本原因

**recorder はセッション単位ではなく WebSocket 接続単位で生成される。**

`backend/routes/whisper.py:714`

```python
recorder = AsyncWavAppender(
    CrashSafeWavWriter(session_store.raw_audio_path(session.config.output_folder))
)
```

この生成は WS ハンドラの中にあり、`finally`（`:902-903`）で close される。
したがって**再接続のたびに、同じ WAV に対する recorder が新しく作られる**。
接続が重なれば、同じファイルに複数の recorder が同時に開いた状態になる。

`CrashSafeWavWriter` は自分が書いた量だけを `_data_bytes` に持ち、
close 時にその値でヘッダを書き戻す（`wav_recorder.py:110-117`）。

```python
def close(self) -> None:
    with self._lock:
        if self._fh is None:
            return
        try:
            self._rewrite_sizes_locked()   # ← _data_bytes をそのまま書く
        finally:
            self._fh.close()
```

破壊に至る順序。

1. 古い recorder R1 が途中まで書き、close されないまま残る（接続が残存）
2. 新しい recorder R2 が同じファイルを開いて追記する。R1 の `_data_bytes` は増えない
3. 停止処理で `repair_wav_header` が**実ファイル長**からヘッダを正しく直す
4. Backend 終了時、残っていた **R1 の close が走り、古い `_data_bytes` でヘッダを上書き**
5. 修復済みの長さが巻き戻り、差分の末尾が再生できなくなる

**`repair_audio` の後に古い recorder が close される順序**が問題である。

## 修正

`backend/services/wav_recorder.py` に `_declared_data_bytes_locked()` を追加し、
ヘッダへ書く長さを **`max(自分の _data_bytes, 実ファイル長 - 44)`** とした。

```python
def _declared_data_bytes_locked(self) -> int:
    try:
        actual = os.fstat(self._fh.fileno()).st_size - RIFF_HEADER_SIZE
    except OSError:
        actual = 0
    return max(self._data_bytes, max(0, actual))
```

`_rewrite_sizes_locked()` がこの値を使う。これにより

- **宣言長が縮むことがなくなる**（巻き戻りが構造的に起きない）
- 定期同期（10 秒ごと）はそのまま動く。単一 writer では `actual == _data_bytes` になる
- close を何度呼んでも安全（既存の `_fh is None` ガードと併せて冪等）
- Backend 再起動・再接続の経路でも安全

A-1 で検証済みの窓処理・確定処理（`plan_window` / `advance_cursor` /
`drain_on_stop` / word commit）には触れていない。

### 修正2: recorder の所有権管理（根本原因）

防御層だけでは「停止後も FD が残る」「複数 recorder が同居しうる」
「古い接続が停止後に追記できる」が残るため、所有権を導入した。

`wav_recorder.py` に `RecorderRegistry` を追加し、**WAV パス単位で
書き込み可能な recorder を同時に 1 つだけ**に保つ。

| メソッド | 役割 |
| --- | --- |
| `acquire(path)` | 所有権を取る。**既存の所有者を flush・close してから**新規生成する |
| `release(recorder)` | 自分の recorder を close する。所有者が入れ替わっていれば登録は触らない |
| `close_all()` | Backend 終了時に残存所有者をすべて閉じる |
| `owner(path)` / `__len__` | 検査用 |

`routes/whisper.py` の変更。

- 生成を `recorder_registry.acquire(...)` へ（`:716`）
- **`session.finalize()` の前に `recorder_registry.release(...)`**（`:800`）。
  `session.json` が `done` になる前に FD が解放される
- `finally` を `recorder_registry.release(recorder)` へ（`:911`）。
  遅れて走っても、所有者が入れ替わっていれば新しい recorder を巻き込まない

`AsyncWavAppender.append` は close 後に早期 return するため、
停止後の追記はファイルへ届かない。`closed` プロパティを検査用に追加した。

既存の `LiveSessionRegistry`（セッション所有権）と同じ流儀に揃えており、
セッション管理の構造を変えていない。

## 回帰テスト

`backend/tests/test_wav_recorder.py::StaleRecorderHeaderRollbackTest`（4 件）

| テスト | 内容 |
| --- | --- |
| `test_old_recorder_close_does_not_shrink_repaired_header` | 本番と同じ順序を再現。**修正前は `32000 != 48000` で失敗**した |
| `test_close_never_declares_less_than_actual_file_length` | 別経路の追記後に close しても縮まない。**修正前は `1000 != 4000` で失敗** |
| `test_close_is_idempotent` | close を 3 回呼んでも宣言長が変わらない |
| `test_periodic_sync_still_updates_header` | 既存の定期ヘッダ更新を壊していない |

`backend/tests/test_wav_recorder.py::RecorderOwnershipTest`（8 件）

| テスト | 内容 |
| --- | --- |
| `test_second_acquire_leaves_only_one_owner` | 2 接続でも所有者は 1 つ |
| `test_reacquire_closes_previous_recorder` | 再接続で旧 recorder が閉じられる |
| `test_release_leaves_no_open_fd` | 停止後に対象ファイルの FD が 0（`lsof` で実測） |
| `test_append_after_release_is_rejected` | 停止後の追記がファイルへ届かない |
| `test_late_release_of_old_recorder_does_not_touch_new_owner` | 遅れた finally が新所有者を巻き込まない |
| `test_release_is_idempotent` | 複数回の release / close が安全 |
| `test_shutdown_after_repair_keeps_header` | repair 後の shutdown でヘッダが変わらない |
| `test_file_can_be_renamed_after_release` | 停止後にファイルを排他的に移動できる |

## 証拠保全

破損した実物を次に保存した（元ファイルと SHA-256 一致を確認済み）。

```
audio/recording.before_0013_repair.wav
SHA-256: 85027c06938ef7fb729d1ad9cca8dd92d5901dc10d5c0e2c7fc9444103bcd892
size:    7,675,948 bytes
mtime:   2026-08-28 10:37:05
```

## 実ファイルの復旧（2026-08-28 12:01）

Backend を起動せず `repair_wav_header` を直接呼び、元ファイルのみを修復した。

| 項目 | 修復前 | 修復後 |
| --- | --- | --- |
| file size | 7,675,948 | 7,675,948（変化なし） |
| RIFF chunk size | 7,569,444 | **7,675,940** |
| data chunk size | 7,569,408 | **7,675,904** |
| `afinfo` | 236.544 秒 | **239.872 秒** |
| SHA-256 | `85027c06…` | `acbdb2e0…`（ヘッダ変更のため変化） |

復旧した末尾 3.328 秒に実音声を確認（mean -30.8 dB / max -15.3 dB）。
先頭・中盤・末尾ともデコード成功。証拠コピーは SHA-256・サイズ・mtime とも無変更。

## 受け入れ試験への影響

E-2 の合格は維持する。E-2 の合格条件は試験時点（2026-08-28 09:41）で成立していた
（修復前 171.904 秒・kill 時点との差 6.096 秒・修復後 239.872 秒が実サンプル時間と一致）。
本件はその後のアプリ終了時に発生した別事象である。
