/**
 * 録音中の入力レベル表示（0019）。
 *
 * 瞬間ピークで激しく点滅しないよう表示値を平滑化し、
 * 色だけに依存しない状態表示（アイコン + 短い文字）を作る。
 *
 * 320px 幅を壊さないよう、テキストは最短の語だけにする。
 */

import { TARGET_SPEECH_DBFS } from './inputProfile';

/** 表示上の下限。これ以下は「無音」として一律に扱う。 */
export const METER_FLOOR_DBFS = -60;
/** メーターの上端。0 dBFS まで描くと通常の会話が左端に潰れる。 */
export const METER_CEIL_DBFS = -6;

/** 平滑化の係数。上昇は速く、下降はゆっくり（メーターの定石）。 */
export const RISE_ALPHA = 0.5;
export const FALL_ALPHA = 0.12;

export type MeterState = 'silent' | 'low' | 'good' | 'loud';

export interface MeterView {
  /** 平滑化後のレベル（dBFS）。 */
  dbfs: number;
  /** バーの塗り幅 0..1。 */
  ratio: number;
  state: MeterState;
  /** 色だけに頼らないための記号。 */
  icon: string;
  /** 320px でも折り返さない短い語。 */
  label: string;
  /** ツールチップ等に出す説明。 */
  title: string;
}

export const STATE_ICON: Record<MeterState, string> = {
  silent: '—',
  low: '▽',
  good: '●',
  loud: '▲'
};

export const STATE_LABEL: Record<MeterState, string> = {
  silent: '無音',
  low: '小さい',
  good: '良好',
  loud: '大きい'
};

/** 振幅（RMS 0..1）を dBFS へ。0 以下は下限値。 */
export function toDbfs(amplitude: number): number {
  const value = Number(amplitude);
  if (!Number.isFinite(value) || value <= 0) return METER_FLOOR_DBFS;
  return Math.max(METER_FLOOR_DBFS, 20 * Math.log10(value));
}

/**
 * 表示値を平滑化する。
 *
 * 上昇と下降で係数を変える。同じ係数だと、上昇に追従させると下降も速くなり
 * バーがちらつく。
 */
export function smoothDbfs(previous: number, next: number): number {
  if (!Number.isFinite(previous)) return next;
  const alpha = next > previous ? RISE_ALPHA : FALL_ALPHA;
  return previous + (next - previous) * alpha;
}

/** dBFS をバーの塗り幅 0..1 へ。 */
export function toRatio(dbfs: number): number {
  const span = METER_CEIL_DBFS - METER_FLOOR_DBFS;
  return Math.min(1, Math.max(0, (dbfs - METER_FLOOR_DBFS) / span));
}

/**
 * レベルの状態を決める。
 *
 * 判定は**平滑化後**の値で行う。生のピークで判定すると、
 * 一瞬の物音で「大きい」が点滅する。
 */
export function classifyLevel(dbfs: number): MeterState {
  if (dbfs <= METER_FLOOR_DBFS + 1) return 'silent';
  // 目標発話レベル -24 dBFS を中心に、±12 dB を「良好」とする。
  if (dbfs < TARGET_SPEECH_DBFS - 12) return 'low';
  if (dbfs > TARGET_SPEECH_DBFS + 12) return 'loud';
  return 'good';
}

/** 平滑化 → 状態判定 → 表示値、をまとめて行う。 */
export function buildMeterView(smoothed: number): MeterView {
  const state = classifyLevel(smoothed);
  return {
    dbfs: smoothed,
    ratio: toRatio(smoothed),
    state,
    icon: STATE_ICON[state],
    label: STATE_LABEL[state],
    title: `入力レベル ${formatDbfs(smoothed)}（${STATE_LABEL[state]}）`
  };
}

/** dBFS の表示。**必ず dBFS と書く**（物理音圧 dB SPL ではない）。 */
export function formatDbfs(dbfs: number): string {
  if (!Number.isFinite(dbfs) || dbfs <= METER_FLOOR_DBFS) return '−∞ dBFS';
  return `${dbfs.toFixed(1)} dBFS`;
}

/** 平滑化の状態を持つ小さなヘルパ。React 側は値を渡すだけにする。 */
export function createLevelSmoother(initial: number = METER_FLOOR_DBFS) {
  let value = initial;
  return {
    /** RMS 振幅を渡して、表示用の View を得る。 */
    push(amplitude: number): MeterView {
      value = smoothDbfs(value, toDbfs(amplitude));
      return buildMeterView(value);
    },
    /** 録音停止時などに下限へ戻す。 */
    reset(): void {
      value = initial;
    },
    get dbfs(): number {
      return value;
    }
  };
}
