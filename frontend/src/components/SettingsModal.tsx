import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type CaptureSettings,
  type DraftErrors,
  type LiveDelayValue,
  type LiveModelValue,
  applyPickedFolder,
  commitDraft,
  createDraft,
  hasChanges,
  hasErrors,
  updateDraft,
  validateDraft
} from './settingsDraft';
import { selectValueForMics } from './deviceNotice';
import {
  INPUT_MODES,
  INPUT_MODE_HINTS,
  INPUT_MODE_LABELS,
  MAX_GAIN_DB,
  MAX_WARNING_SECONDS,
  MIN_WARNING_SECONDS,
  describeResolution,
  normalizeProfile,
  profileForDevice,
  resolveMode,
  type GainMode,
  type InputMode,
  type SilenceMode
} from '../features/audio/inputProfile';
import { CalibrationModal } from './CalibrationModal';
import {
  MAX_WINDOW_OPACITY,
  MIN_WINDOW_OPACITY,
  WINDOW_OPACITY_STEP,
  opacityToPercent,
  percentToOpacity
} from './windowOpacity';

interface Props {
  open: boolean;
  current: CaptureSettings;
  mics: MediaDeviceInfo[];
  recording: boolean;
  /** フォルダ選択ダイアログ。キャンセルは null。 */
  onPickFolder: () => Promise<string | null>;
  /** 保存先の存在確認。未接続なら undefined を返してよい。 */
  onCheckFolder: (path: string) => Promise<boolean | undefined>;
  onSave: (next: CaptureSettings) => void;
  onClose: () => void;
  /** スライダー操作中のライブプレビュー。保存とは別に即時反映する（0018）。 */
  onPreviewOpacity: (value: number) => void;
}

/** 数値入力の共通処理。空欄や不正値で NaN を書き込まない。 */
function numberOr(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * マイGPT URL・保存先・入力デバイス・モデル・遅延モードの設定（0015）。
 * 5 項目をひとまとまりの下書きとして扱い、保存を押したときだけ反映する。
 */
export function SettingsModal({
  open,
  current,
  mics,
  recording,
  onPickFolder,
  onCheckFolder,
  onSave,
  onClose,
  onPreviewOpacity
}: Props) {
  const [draft, setDraft] = useState<CaptureSettings>(() => createDraft(current));
  const [errors, setErrors] = useState<DraftErrors>({});
  const [calibrationOpen, setCalibrationOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  // 0018: モーダルを開いた時点の不透明度。キャンセル / Escape / 背景クリックで戻す。
  const openedOpacityRef = useRef<number>(current.windowOpacity);
  const firstFieldRef = useRef<HTMLInputElement | null>(null);

  // 開くたびに現在値から作り直す。前回の未保存値を持ち越さない。
  useEffect(() => {
    if (open) {
      setDraft(createDraft(current));
      setErrors({});
      openedOpacityRef.current = current.windowOpacity;
    }
  }, [open, current]);

  useEffect(() => {
    if (open) firstFieldRef.current?.focus();
  }, [open]);

  const close = useCallback(() => {
    // 未保存値は破棄する。プレビュー中の不透明度も開いた時点へ戻す（0018）。
    onPreviewOpacity(openedOpacityRef.current);
    setDraft(createDraft(current));
    setErrors({});
    onClose();
  }, [current, onClose, onPreviewOpacity]);

  // Escape で閉じる。フォーカスをモーダル内に留める。
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;
      const root = dialogRef.current;
      if (!root) return;
      const focusable = root.querySelectorAll<HTMLElement>(
        'button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, close]);

  const pick = useCallback(async () => {
    const picked = await onPickFolder().catch(() => null);
    // キャンセル時は下書きを変えない。
    setDraft((d) => applyPickedFolder(d, picked));
    if (picked) setErrors((e) => ({ ...e, saveFolder: undefined }));
  }, [onPickFolder]);

  const save = useCallback(async () => {
    const folder = String(draft.saveFolder ?? '').trim();
    const exists = folder ? await onCheckFolder(folder).catch(() => undefined) : undefined;
    const found = validateDraft(draft, exists);
    setErrors(found);
    if (hasErrors(found)) {
      // 保存できないので、プレビュー中の不透明度は開いた時点へ戻す（0018）。
      onPreviewOpacity(openedOpacityRef.current);
      return;
    }
    // 変更がなければ書き込まない。
    if (hasChanges(current, draft)) {
      const next = commitDraft(current, draft, recording, found);
      onSave(next);
      // 録音中は commitDraft が現在値を返す。プレビューもその値へ揃える。
      onPreviewOpacity(next.windowOpacity);
    } else {
      onPreviewOpacity(openedOpacityRef.current);
    }
    onClose();
  }, [current, draft, onCheckFolder, onClose, onSave, recording]);

  if (!open) return null;

  // 0019: 自動判定の結果は常に UI へ出す（ユーザーが手動で上書きできることが前提）。
  const resolution = resolveMode(draft.audio.inputMode, draft.deviceLabel);
  const isCustom = draft.audio.inputMode === 'custom';

  return (
    <div className="modal-backdrop" onClick={close}>
      <div
        className="modal settings-modal"
        role="dialog"
        aria-modal="true"
        aria-label="マイGPT・保存先・入力の設定"
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
      >
        <h3>設定</h3>
        {recording ? (
          <p className="hint hint-error">録音中は変更できません。停止してから設定してください。</p>
        ) : null}

        <div className="settings-field">
          <label htmlFor="set-gpturl">マイGPTのURL</label>
          <input
            id="set-gpturl"
            ref={firstFieldRef}
            type="text"
            aria-label="マイGPTのURL"
            title={draft.gptUrl || 'マイGPTのURL'}
            placeholder="https://chatgpt.com/g/g-xxxxxxxx"
            value={draft.gptUrl}
            disabled={recording}
            className={errors.gptUrl ? 'invalid' : ''}
            onChange={(e) => setDraft((d) => updateDraft(d, 'gptUrl', e.target.value))}
          />
          {errors.gptUrl ? <p className="hint hint-error">{errors.gptUrl}</p> : null}
        </div>

        <div className="settings-field">
          <label htmlFor="set-folder">文字起こしファイル保存先</label>
          <div className="settings-inline">
            <input
              id="set-folder"
              type="text"
              aria-label="文字起こしファイル保存先"
              title={draft.saveFolder || '文字起こしファイル保存先'}
              placeholder="/Users/you/Documents/KoeNote"
              value={draft.saveFolder}
              disabled={recording}
              className={errors.saveFolder ? 'invalid' : ''}
              onChange={(e) => setDraft((d) => updateDraft(d, 'saveFolder', e.target.value))}
            />
            <button
              type="button"
              className="btn-ghost btn-pick"
              onClick={pick}
              disabled={recording}
              aria-label="文字起こしファイル保存先を選択"
              title="保存先フォルダを選択"
            >
              選択
            </button>
          </div>
          {errors.saveFolder ? <p className="hint hint-error">{errors.saveFolder}</p> : null}
        </div>

        <div className="settings-field">
          <label htmlFor="set-mic">入力デバイス</label>
          <select
            id="set-mic"
            // 0016: 保存済み ID が一覧に無いときは空欄にせず「既定の入力デバイス」を選択状態にする。
            value={selectValueForMics(draft.deviceId, mics)}
            disabled={recording}
            aria-label="入力デバイス"
            onChange={(e) => {
              const id = e.target.value;
              // ラベルも一緒に保存する。origin が変わって ID が無効になっても引き当て直せる。
              const label = mics.find((m) => m.deviceId === id)?.label ?? '';
              setDraft((d) => {
                // 0019: デバイスを変えたら、そのデバイス用のプリセットを解決し直す。
                // 旧デバイスの設定（BlackHole 用の「補正なし」など）を持ち込まない。
                const audio = profileForDevice(d.inputProfiles, label);
                return updateDraft(
                  updateDraft(updateDraft(d, 'deviceId', id), 'deviceLabel', label),
                  'audio',
                  audio
                );
              });
            }}
          >
            <option value="">既定の入力デバイス</option>
            {mics.map((m, i) => (
              <option key={m.deviceId || i} value={m.deviceId}>{m.label || `マイク ${i + 1}`}</option>
            ))}
          </select>
        </div>

        {/* 0019: 入力音声。一般利用者向けの「基本」と、RMS などを扱う「詳細」を分ける。 */}
        <div className="settings-section">
          <h3 className="settings-section-title">入力音声</h3>

          <div className="settings-field">
            <label htmlFor="set-input-mode">入力モード</label>
            <select
              id="set-input-mode"
              value={draft.audio.inputMode}
              disabled={recording}
              aria-label="入力モード"
              onChange={(e) => {
                const mode = e.target.value as InputMode;
                setDraft((d) =>
                  updateDraft(d, 'audio', normalizeProfile(
                    { ...d.audio, inputMode: mode }, d.deviceLabel
                  ))
                );
              }}
            >
              {INPUT_MODES.map((mode) => (
                <option key={mode} value={mode}>{INPUT_MODE_LABELS[mode]}</option>
              ))}
            </select>
            {/* 自動判定の結果は必ず見せる。ユーザーが手動で上書きできることが前提。 */}
            <p className="hint">{describeResolution(resolution, draft.deviceLabel)}</p>
            <p className="hint">{INPUT_MODE_HINTS[draft.audio.inputMode]}</p>
          </div>

          <div className="settings-field">
            <label className="settings-check">
              <input
                type="checkbox"
                checked={draft.audio.lowInputWarning}
                disabled={recording}
                onChange={(e) =>
                  setDraft((d) =>
                    updateDraft(d, 'audio', { ...d.audio, lowInputWarning: e.target.checked })
                  )
                }
              />
              <span>入力音量が低いときに警告する</span>
            </label>
          </div>

          <div className="settings-field">
            <button
              type="button"
              className="btn-ghost"
              disabled={recording}
              onClick={() => setCalibrationOpen(true)}
            >
              入力テストを実行
            </button>
            <p className="hint">
              最も遠い席からの声が文字起こしできるかを測定します（約30秒）。
              録音セッションは作成されず、テスト音声も保存されません。
            </p>
          </div>

          <button
            type="button"
            className="btn-link"
            aria-expanded={draft.showAdvancedAudio}
            onClick={() =>
              setDraft((d) => updateDraft(d, 'showAdvancedAudio', !d.showAdvancedAudio))
            }
          >
            {draft.showAdvancedAudio ? '詳細設定を隠す' : '詳細設定を表示'}
          </button>

          {draft.showAdvancedAudio ? (
            <div className="settings-advanced">
              <p className="hint">
                以下はカスタムモードでのみ変更できます。数値は dBFS
                （デジタルのフルスケール基準）であり、騒音計の dB SPL とは異なります。
              </p>

              <div className="settings-field">
                <label htmlFor="set-gain-mode">音量補正</label>
                <select
                  id="set-gain-mode"
                  value={draft.audio.gainMode}
                  disabled={recording || !isCustom}
                  aria-label="音量補正"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', { ...d.audio, gainMode: e.target.value as GainMode })
                    )
                  }
                >
                  <option value="auto">自動</option>
                  <option value="none">なし（0dB）</option>
                  <option value="manual">手動</option>
                </select>
              </div>

              <div className="settings-field">
                <div className="opacity-head">
                  <label htmlFor="set-manual-gain">手動ゲイン</label>
                  <span className="opacity-value mono">
                    +{draft.audio.manualGainDb.toFixed(0)} dB
                  </span>
                </div>
                <input
                  id="set-manual-gain"
                  type="range"
                  className="opacity-range"
                  min={0}
                  max={MAX_GAIN_DB}
                  step={1}
                  value={draft.audio.manualGainDb}
                  disabled={recording || !isCustom || draft.audio.gainMode !== 'manual'}
                  aria-label="手動ゲイン（dB）"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', {
                        ...d.audio,
                        manualGainDb: numberOr(e.target.value, 0)
                      })
                    )
                  }
                />
              </div>

              <div className="settings-field">
                <div className="opacity-head">
                  <label htmlFor="set-max-gain">最大ゲイン</label>
                  <span className="opacity-value mono">
                    +{draft.audio.maxGainDb.toFixed(0)} dB
                  </span>
                </div>
                <input
                  id="set-max-gain"
                  type="range"
                  className="opacity-range"
                  min={0}
                  max={MAX_GAIN_DB}
                  step={1}
                  value={draft.audio.maxGainDb}
                  disabled={recording || !isCustom}
                  aria-label="最大ゲイン（dB）"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', {
                        ...d.audio,
                        maxGainDb: numberOr(e.target.value, MAX_GAIN_DB)
                      })
                    )
                  }
                />
                <p className="hint">自動補正の上限。ノイズを持ち上げすぎないための安全弁です。</p>
              </div>

              <div className="settings-field">
                <label htmlFor="set-silence-mode">無音判定</label>
                <select
                  id="set-silence-mode"
                  value={draft.audio.silenceMode}
                  disabled={recording || !isCustom}
                  aria-label="無音判定"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', {
                        ...d.audio,
                        silenceMode: e.target.value as SilenceMode
                      })
                    )
                  }
                >
                  <option value="relative">自動（ノイズフロア基準の相対判定）</option>
                  <option value="absolute">絶対下限のみ</option>
                  <option value="manual">手動しきい値</option>
                </select>
              </div>

              <div className="settings-field">
                <label htmlFor="set-silence-rms">手動しきい値（RMS）</label>
                <input
                  id="set-silence-rms"
                  type="number"
                  min={0}
                  max={0.05}
                  step={0.0001}
                  value={draft.audio.manualSilenceRms}
                  disabled={recording || !isCustom || draft.audio.silenceMode !== 'manual'}
                  aria-label="手動しきい値（RMS）"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', {
                        ...d.audio,
                        manualSilenceRms: numberOr(e.target.value, 0.0002)
                      })
                    )
                  }
                />
                <p className="hint">
                  既定 0.0002（-74 dBFS）。これより大きくすると遠い話者を取りこぼします。
                </p>
              </div>

              <div className="settings-field">
                <label htmlFor="set-warn-seconds">警告までの継続時間（秒）</label>
                <input
                  id="set-warn-seconds"
                  type="number"
                  min={MIN_WARNING_SECONDS}
                  max={MAX_WARNING_SECONDS}
                  step={1}
                  value={draft.audio.lowInputWarningSeconds}
                  disabled={recording}
                  aria-label="警告までの継続時間（秒）"
                  onChange={(e) =>
                    setDraft((d) =>
                      updateDraft(d, 'audio', {
                        ...d.audio,
                        lowInputWarningSeconds: numberOr(e.target.value, 20)
                      })
                    )
                  }
                />
              </div>
            </div>
          ) : null}
        </div>

        <div className="settings-field">
          <label htmlFor="set-model">モデル</label>
          <select
            id="set-model"
            value={draft.model}
            disabled={recording}
            aria-label="Whisper モデル"
            onChange={(e) => setDraft((d) => updateDraft(d, 'model', e.target.value as LiveModelValue))}
          >
            <option value="tiny">tiny</option>
            <option value="base">base</option>
            <option value="small">small（推奨）</option>
            <option value="medium">medium</option>
          </select>
        </div>

        <div className="settings-field">
          <label htmlFor="set-delay">遅延モード</label>
          <select
            id="set-delay"
            value={draft.delayMode}
            disabled={recording}
            aria-label="遅延モード"
            onChange={(e) => setDraft((d) => updateDraft(d, 'delayMode', e.target.value as LiveDelayValue))}
          >
            <option value="low_latency">低遅延 (8s/2s)</option>
            <option value="balanced">標準 (10s/2s)</option>
            <option value="accuracy">精度優先 (12s/3s)</option>
          </select>
        </div>

        {/* 0018: ラベルと現在値を 1 行に置き、スライダーは次行いっぱいに広げる。
            320px の設定モーダルでも見切れない。 */}
        <div className="settings-field">
          <div className="opacity-head">
            <label htmlFor="set-opacity">ウィンドウの不透明度</label>
            <span className="opacity-value mono">{opacityToPercent(draft.windowOpacity)}%</span>
          </div>
          <input
            id="set-opacity"
            type="range"
            className="opacity-range"
            min={Math.round(MIN_WINDOW_OPACITY * 100)}
            max={Math.round(MAX_WINDOW_OPACITY * 100)}
            step={Math.round(WINDOW_OPACITY_STEP * 100)}
            value={opacityToPercent(draft.windowOpacity)}
            disabled={recording}
            aria-label="ウィンドウの不透明度（パーセント）"
            onChange={(e) => {
              const next = percentToOpacity(Number(e.target.value));
              setDraft((d) => updateDraft(d, 'windowOpacity', next));
              // 操作中は即座にウィンドウへ反映する（保存はしない）。
              onPreviewOpacity(next);
            }}
          />
          <p className="hint">100% = 透過なし</p>
        </div>

        <div className="modal-actions">
          <button type="button" className="btn-accent" disabled={recording} onClick={save}>保存</button>
          <button type="button" className="btn-ghost" onClick={close}>キャンセル</button>
        </div>
      </div>

      <CalibrationModal
        open={calibrationOpen}
        deviceId={draft.deviceId}
        deviceLabel={draft.deviceLabel}
        settings={draft.audio}
        onClose={() => setCalibrationOpen(false)}
        onApply={(next) => setDraft((d) => updateDraft(d, 'audio', next))}
      />
    </div>
  );
}
