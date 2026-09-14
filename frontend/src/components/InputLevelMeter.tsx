/**
 * 録音中の入力レベルメーター（0019）。
 *
 * 320px 幅を壊さないよう 1 行に収め、常時大きな領域を占有しない。
 * 色だけに依存しないよう、記号（—/▽/●/▲）と短い語も出す。
 */
import type { MeterView } from '../features/audio/levelMeter';
import { formatDbfs } from '../features/audio/levelMeter';
import type { ResolvedMode } from '../features/audio/inputProfile';
import { INPUT_MODE_LABELS } from '../features/audio/inputProfile';

export interface InputLevelMeterProps {
  view: MeterView;
  /** 適用中の入力モード。簡潔に出す。 */
  mode: ResolvedMode;
  /** 自動判定をそのまま使っているか。 */
  isAuto: boolean;
  /** 補正で掛かっているゲイン（dB）。0 なら出さない。 */
  gainDb?: number | null;
  compact?: boolean;
}

export function InputLevelMeter({ view, mode, isAuto, gainDb, compact }: InputLevelMeterProps) {
  const modeLabel = INPUT_MODE_LABELS[mode] ?? mode;
  const gain = typeof gainDb === 'number' && Number.isFinite(gainDb) && gainDb >= 0.5
    ? `+${gainDb.toFixed(0)}dB`
    : null;
  const title =
    `${view.title} / 入力モード: ${modeLabel}${isAuto ? '（自動判定）' : ''}` +
    (gain ? ` / 自動補正 ${gain}` : '');

  return (
    <div
      className={`input-level ${compact ? 'input-level-compact' : ''}`}
      title={title}
      role="group"
      aria-label="入力レベル"
    >
      <span className={`input-level-state input-level-${view.state}`} aria-hidden="true">
        {view.icon}
      </span>
      <span className="input-level-bar">
        <span
          className={`input-level-fill input-level-${view.state}`}
          style={{ width: `${Math.round(view.ratio * 100)}%` }}
        />
      </span>
      <span className="input-level-text mono">
        <span className="input-level-label">{view.label}</span>
        {!compact ? <span className="input-level-db">{formatDbfs(view.dbfs)}</span> : null}
      </span>
      <span className="input-level-mode" title={title}>
        {modeLabel}
        {gain ? <span className="input-level-gain">{gain}</span> : null}
      </span>
    </div>
  );
}
