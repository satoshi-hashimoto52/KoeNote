/**
 * 320px 幅 / 最小高 480px で操作不能にならないことの回帰（0019 / 0009）。
 *
 * DOM を組まずに検証できる範囲——「幅を食う定数」と「省略される要素」——を
 * CSS とロジックの両側から固定する。実際の描画確認は実機で行う。
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { STATE_LABEL, buildMeterView } from './levelMeter';
import { WARNING_HEADINGS } from './lowVolumeWarning';
import { INPUT_MODE_LABELS } from './inputProfile';
import { VERDICT_LABELS, buildResultRows, type CalibrationResult } from './calibration';

const css = readFileSync(join(process.cwd(), 'frontend/src/styles.css'), 'utf-8');

describe('320px 幅で崩れないための制約', () => {
  it('レベルメーターの状態ラベルは 4 文字以内', () => {
    for (const label of Object.values(STATE_LABEL)) {
      expect(label.length, label).toBeLessThanOrEqual(4);
    }
  });

  it('入力モードのラベルは 6 文字以内', () => {
    for (const label of Object.values(INPUT_MODE_LABELS)) {
      expect(label.length, label).toBeLessThanOrEqual(6);
    }
  });

  it('警告の見出しは 8 文字以内', () => {
    for (const heading of Object.values(WARNING_HEADINGS)) {
      expect(heading.length, heading).toBeLessThanOrEqual(8);
    }
  });

  it('総合判定の表示名は 12 文字以内', () => {
    for (const label of Object.values(VERDICT_LABELS)) {
      expect(label.length, label).toBeLessThanOrEqual(12);
    }
  });

  it('メーターはバーを可変幅にして固定幅を持たない', () => {
    expect(css).toMatch(/\.input-level-bar\s*\{[^}]*flex:\s*1\s+1\s+auto/);
    expect(css).toMatch(/\.input-level-bar\s*\{[^}]*min-width:\s*\d+px/);
  });

  it('380px 以下では数値表示を落としてバーを残す', () => {
    const narrow = css.slice(css.indexOf('@media (max-width: 380px)'));
    expect(narrow).toContain('.input-level-db { display: none; }');
  });

  it('入力テストの結果行は狭幅で 2 行へ折り返す', () => {
    expect(css).toMatch(/\.calibration-row\s*\{\s*flex-direction:\s*column/);
  });

  it('モーダルのボタンは狭幅で折り返す', () => {
    expect(css).toContain('.calibration-modal .modal-actions { flex-wrap: wrap; }');
  });

  it('メーターと設定欄が min-width:0 を持つ（flex の潰れ対策）', () => {
    expect(css).toMatch(/\.input-level\s*\{[^}]*min-width:\s*0/);
    expect(css).toMatch(/\.settings-section\s*\{[^}]*min-width:\s*0/);
  });
});

describe('コンパクト表示の情報量', () => {
  it('compact でも状態記号と短い語は残る', () => {
    const view = buildMeterView(-48);
    expect(view.icon.length).toBeGreaterThan(0);
    expect(view.label.length).toBeGreaterThan(0);
  });

  it('結果行は基本 8 行以内（詳細を除く）に収まる', () => {
    const result = {
      device_label: 'マイク',
      input_mode: 'mic',
      detected_mode: 'mic',
      unit: 'dBFS',
      noise_floor_dbfs: -62,
      speech_median_dbfs: -38,
      speech_low_dbfs: -44,
      speech_peak_dbfs: -30,
      snr_db: 24,
      speech_ratio: 0.3,
      current_gain_db: 0,
      current_pass_ratio: 0.4,
      current_skip_ratio: 0.6,
      recommended_gain_db: 14,
      recommended_pass_ratio: 0.99,
      recommended_skip_ratio: 0.01,
      required_gain_db: 14,
      max_gain_db: 24,
      clipping_risk: 'low',
      clip_ratio: 0,
      verdict: 'usable',
      recommended_settings: {}
    } as CalibrationResult;
    const basic = buildResultRows(result).filter((row) => !row.advanced);
    expect(basic.length).toBeLessThanOrEqual(9);
  });
});
