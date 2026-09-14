import { describe, expect, it } from 'vitest';
import {
  NOISE_STEP_SECONDS,
  SPEECH_STEP_SECONDS,
  STEP_INSTRUCTIONS,
  STEP_TITLES,
  TEST_SENTENCE,
  VERDICT_ADVICE,
  VERDICT_LABELS,
  VERDICT_TONES,
  applyRecommended,
  buildResultRows,
  compareRuns,
  describeImprovement,
  encodePcmChunks,
  formatDb,
  formatDbfs,
  formatPercent,
  remainingSeconds,
  stepProgress,
  type CalibrationResult
} from './calibration';
import { DEFAULT_PROFILE } from './inputProfile';

function result(over: Partial<CalibrationResult> = {}): CalibrationResult {
  return {
    device_label: 'MacBook Pro のマイク',
    input_mode: 'mic',
    detected_mode: 'mic',
    unit: 'dBFS',
    noise_floor_dbfs: -62,
    speech_median_dbfs: -38,
    speech_low_dbfs: -44,
    speech_peak_dbfs: -30,
    snr_db: 24,
    speech_ratio: 0.32,
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
    recommended_settings: { inputMode: 'mic', gainMode: 'auto', maxGainDb: 17 },
    ...over
  };
}

describe('テスト文と手順', () => {
  it('指定されたテスト文をそのまま持つ', () => {
    expect(TEST_SENTENCE).toContain('これはKoeNoteのマイク入力テストです。');
    expect(TEST_SENTENCE).toContain('会議中と同じ声量で、普段どおりに話しています。');
    expect(TEST_SENTENCE).toContain('離れた位置からでも、音声が正しく認識されるか確認します。');
  });

  it('環境音は5秒、発話は15〜20秒', () => {
    expect(NOISE_STEP_SECONDS).toBe(5);
    expect(SPEECH_STEP_SECONDS).toBeGreaterThanOrEqual(15);
    expect(SPEECH_STEP_SECONDS).toBeLessThanOrEqual(20);
  });

  it('各ステップに見出しと案内がある', () => {
    expect(STEP_TITLES.noise).toContain('環境音');
    expect(STEP_TITLES.speech).toContain('遠方発話');
    expect(STEP_INSTRUCTIONS.noise).toContain('話さずにお待ちください');
    expect(STEP_INSTRUCTIONS.speech).toContain('最も遠い席');
  });
});

describe('進捗とカウントダウン', () => {
  it('経過に応じて 0..1 を返す', () => {
    expect(stepProgress('noise', 0)).toBe(0);
    expect(stepProgress('noise', 2500)).toBeCloseTo(0.5, 2);
    expect(stepProgress('noise', 99_999)).toBe(1);
    expect(stepProgress('result', 1000)).toBe(0);
  });

  it('残り秒を切り上げで返す', () => {
    expect(remainingSeconds('noise', 0)).toBe(5);
    expect(remainingSeconds('noise', 4_100)).toBe(1);
    expect(remainingSeconds('noise', 5_000)).toBe(0);
    expect(remainingSeconds('speech', 0)).toBe(SPEECH_STEP_SECONDS);
  });
});

describe('数値表示', () => {
  it('dBFS と表記し dB SPL とは書かない', () => {
    expect(formatDbfs(-38)).toBe('-38.0 dBFS');
    expect(formatDbfs(-38)).not.toContain('SPL');
    expect(formatDbfs(-200)).toBe('−∞ dBFS');
  });

  it('SNR は相対量なので dB 表記', () => {
    expect(formatDb(24)).toBe('+24.0 dB');
    expect(formatDb(-3.2)).toBe('-3.2 dB');
    expect(formatDb(Number.NaN)).toBe('—');
  });

  it('比率はパーセント表示', () => {
    expect(formatPercent(0.995)).toBe('100%');
    expect(formatPercent(0.4)).toBe('40%');
    expect(formatPercent(null)).toBe('—');
  });
});

describe('buildResultRows', () => {
  it('指示された項目をすべて出す', () => {
    const rows = buildResultRows(result());
    const labels = rows.map((r) => r.label);
    for (const expected of [
      '入力デバイス',
      '判定された入力モード',
      'ノイズフロア',
      '発話レベル（中央値）',
      '小さい発話（下位20%）',
      '発話ピーク',
      'SNR',
      '現在設定での推定通過率',
      '推定スキップ率',
      '必要な推奨ゲイン',
      'クリッピングリスク'
    ]) {
      expect(labels, expected).toContain(expected);
    }
  });

  it('中央値・パーセンタイルを明示する（最大最小だけで判定していない）', () => {
    const labels = buildResultRows(result()).map((r) => r.label);
    expect(labels.some((l) => l.includes('中央値'))).toBe(true);
    expect(labels.some((l) => l.includes('下位20%'))).toBe(true);
  });

  it('生の数値は詳細設定側へ寄せる', () => {
    const rows = buildResultRows(result());
    const noise = rows.find((r) => r.label === 'ノイズフロア');
    expect(noise?.advanced).toBe(true);
  });

  it('自動判定と手動選択を区別して書く', () => {
    const auto = buildResultRows(result()).find((r) => r.label === '判定された入力モード');
    expect(auto?.value).toContain('自動判定');
    const manual = buildResultRows(
      result({ input_mode: 'mic', detected_mode: 'loopback' })
    ).find((r) => r.label === '判定された入力モード');
    expect(manual?.value).toContain('手動選択');
  });
});

describe('総合判定', () => {
  it('7 種すべてに表示名・トーン・助言がある', () => {
    for (const verdict of [
      'good',
      'usable',
      'needs_adjust',
      'too_quiet',
      'noisy',
      'clipping',
      'no_input'
    ] as const) {
      expect(VERDICT_LABELS[verdict]).toBeTruthy();
      expect(VERDICT_TONES[verdict]).toBeTruthy();
      expect(VERDICT_ADVICE[verdict].length).toBeGreaterThan(5);
    }
  });

  it('指定どおりの日本語表示', () => {
    expect(VERDICT_LABELS.good).toBe('良好');
    expect(VERDICT_LABELS.usable).toBe('使用可能');
    expect(VERDICT_LABELS.needs_adjust).toBe('要調整');
    expect(VERDICT_LABELS.too_quiet).toBe('入力が小さすぎる');
    expect(VERDICT_LABELS.noisy).toBe('環境音が大きすぎる');
    expect(VERDICT_LABELS.clipping).toBe('音割れの可能性');
    expect(VERDICT_LABELS.no_input).toBe('入力を検出できない');
  });
});

describe('describeImprovement', () => {
  it('改善見込みを示す', () => {
    expect(describeImprovement(result())).toContain('40% → 99%');
  });

  it('既に十分なら改善不要と伝える', () => {
    const text = describeImprovement(
      result({ current_pass_ratio: 0.99, recommended_pass_ratio: 0.99 })
    );
    expect(text).toContain('既に');
  });
});

describe('compareRuns', () => {
  it('推奨設定適用後の再測定で改善を検出する', () => {
    const before = result({ current_pass_ratio: 0.4 });
    const after = result({ current_pass_ratio: 0.99 });
    const comparison = compareRuns(before, after);
    expect(comparison.improved).toBe(true);
    expect(comparison.summary).toContain('40% → 99%');
  });

  it('クリッピングが悪化したら改善とみなさない', () => {
    const before = result({ current_pass_ratio: 0.4, clipping_risk: 'low' });
    const after = result({ current_pass_ratio: 0.99, clipping_risk: 'high' });
    expect(compareRuns(before, after).improved).toBe(false);
  });

  it('通過率が上がらなければ改善とみなさない', () => {
    const before = result({ current_pass_ratio: 0.9 });
    const after = result({ current_pass_ratio: 0.9 });
    expect(compareRuns(before, after).improved).toBe(false);
  });
});

describe('applyRecommended', () => {
  it('推奨設定を反映する', () => {
    const next = applyRecommended(DEFAULT_PROFILE, {
      inputMode: 'mic',
      gainMode: 'auto',
      maxGainDb: 17,
      lowInputWarning: true
    });
    expect(next.inputMode).toBe('mic');
    expect(next.maxGainDb).toBe(17);
  });

  it('未知のキーは無視する', () => {
    const next = applyRecommended(DEFAULT_PROFILE, { somethingElse: 1 });
    expect(next).toEqual(DEFAULT_PROFILE);
  });

  it('数値でない値は反映しない', () => {
    const next = applyRecommended(DEFAULT_PROFILE, { maxGainDb: 'oops' });
    expect(next.maxGainDb).toBe(DEFAULT_PROFILE.maxGainDb);
  });
});

describe('encodePcmChunks', () => {
  it('複数チャンクを連結して base64 にする', () => {
    const a = new Uint8Array([1, 2, 3]).buffer;
    const b = new Uint8Array([4, 5]).buffer;
    expect(encodePcmChunks([a, b])).toBe(btoa('\x01\x02\x03\x04\x05'));
  });

  it('空でも壊れない', () => {
    expect(encodePcmChunks([])).toBe('');
  });

  it('大きいデータでもスタックを溢れさせない', () => {
    const big = new Uint8Array(300_000).buffer;
    expect(() => encodePcmChunks([big])).not.toThrow();
  });
});
