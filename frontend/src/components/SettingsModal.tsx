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
  onClose
}: Props) {
  const [draft, setDraft] = useState<CaptureSettings>(() => createDraft(current));
  const [errors, setErrors] = useState<DraftErrors>({});
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const firstFieldRef = useRef<HTMLInputElement | null>(null);

  // 開くたびに現在値から作り直す。前回の未保存値を持ち越さない。
  useEffect(() => {
    if (open) {
      setDraft(createDraft(current));
      setErrors({});
    }
  }, [open, current]);

  useEffect(() => {
    if (open) firstFieldRef.current?.focus();
  }, [open]);

  const close = useCallback(() => {
    // 未保存値は破棄する。
    setDraft(createDraft(current));
    setErrors({});
    onClose();
  }, [current, onClose]);

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
    if (hasErrors(found)) return;
    // 変更がなければ書き込まない。
    if (hasChanges(current, draft)) {
      onSave(commitDraft(current, draft, recording, found));
    }
    onClose();
  }, [current, draft, onCheckFolder, onClose, onSave, recording]);

  if (!open) return null;

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
              placeholder="/Users/you/Documents/BridgeLog"
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
            value={draft.deviceId}
            disabled={recording}
            aria-label="入力デバイス"
            onChange={(e) => setDraft((d) => updateDraft(d, 'deviceId', e.target.value))}
          >
            <option value="">既定入力</option>
            {mics.map((m, i) => (
              <option key={m.deviceId || i} value={m.deviceId}>{m.label || `マイク ${i + 1}`}</option>
            ))}
          </select>
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

        <div className="modal-actions">
          <button type="button" className="btn-accent" disabled={recording} onClick={save}>保存</button>
          <button type="button" className="btn-ghost" onClick={close}>キャンセル</button>
        </div>
      </div>
    </div>
  );
}
