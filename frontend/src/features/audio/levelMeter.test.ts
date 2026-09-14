import { describe, expect, it } from 'vitest';
import {
  METER_CEIL_DBFS,
  METER_FLOOR_DBFS,
  STATE_ICON,
  STATE_LABEL,
  buildMeterView,
  classifyLevel,
  createLevelSmoother,
  formatDbfs,
  smoothDbfs,
  toDbfs,
  toRatio
} from './levelMeter';

describe('toDbfs', () => {
  it('振幅を dBFS へ変換する', () => {
    expect(toDbfs(1)).toBeCloseTo(0, 5);
    expect(toDbfs(0.1)).toBeCloseTo(-20, 5);
    expect(toDbfs(0.01)).toBeCloseTo(-40, 5);
  });

  it('0 や不正値は下限へ落とす', () => {
    expect(toDbfs(0)).toBe(METER_FLOOR_DBFS);
    expect(toDbfs(-1)).toBe(METER_FLOOR_DBFS);
    expect(toDbfs(Number.NaN)).toBe(METER_FLOOR_DBFS);
  });
});

describe('smoothDbfs', () => {
  it('上昇は速く追従する', () => {
    const next = smoothDbfs(-60, -20);
    expect(next).toBeGreaterThan(-45);
  });

  it('下降はゆっくり戻る', () => {
    const next = smoothDbfs(-20, -60);
    expect(next).toBeGreaterThan(-30);
  });

  it('瞬間ピークで表示が振り切れない', () => {
    let value = -40;
    // 1 フレームだけ大きな音が来ても、そのフレームで最大まで行かない
    value = smoothDbfs(value, -6);
    expect(value).toBeLessThan(-20);
  });

  it('同じ値が続けば収束する', () => {
    let value = -60;
    for (let i = 0; i < 60; i += 1) value = smoothDbfs(value, -24);
    expect(value).toBeCloseTo(-24, 1);
  });
});

describe('toRatio', () => {
  it('下限で 0、上限で 1', () => {
    expect(toRatio(METER_FLOOR_DBFS)).toBe(0);
    expect(toRatio(METER_CEIL_DBFS)).toBe(1);
  });

  it('範囲外でも 0..1 に収まる', () => {
    expect(toRatio(-200)).toBe(0);
    expect(toRatio(10)).toBe(1);
  });
});

describe('classifyLevel', () => {
  it('無音・小さい・良好・大きいを区別する', () => {
    expect(classifyLevel(-60)).toBe('silent');
    expect(classifyLevel(-45)).toBe('low');
    expect(classifyLevel(-24)).toBe('good');
    expect(classifyLevel(-8)).toBe('loud');
  });

  it('0019 の生入力レベル（-48 dBFS）は「小さい」と出る', () => {
    expect(classifyLevel(-48)).toBe('low');
  });

  it('補正後の目標レベル（-24 dBFS）は「良好」と出る', () => {
    expect(classifyLevel(-24)).toBe('good');
  });
});

describe('buildMeterView', () => {
  it('色に依存しない記号と短い文字を返す', () => {
    for (const dbfs of [-60, -45, -24, -8]) {
      const view = buildMeterView(dbfs);
      expect(view.icon).toBe(STATE_ICON[view.state]);
      expect(view.label).toBe(STATE_LABEL[view.state]);
      // 320px 幅で折り返さない長さ
      expect(view.label.length).toBeLessThanOrEqual(4);
    }
  });

  it('title に dBFS を含む', () => {
    expect(buildMeterView(-24).title).toContain('dBFS');
  });
});

describe('formatDbfs', () => {
  it('dB SPL ではなく dBFS と書く', () => {
    expect(formatDbfs(-24)).toBe('-24.0 dBFS');
    expect(formatDbfs(-24)).not.toContain('SPL');
  });

  it('下限は −∞ と表示する', () => {
    expect(formatDbfs(METER_FLOOR_DBFS)).toBe('−∞ dBFS');
    expect(formatDbfs(Number.NaN)).toBe('−∞ dBFS');
  });
});

describe('createLevelSmoother', () => {
  it('連続入力で状態が安定する', () => {
    const smoother = createLevelSmoother();
    let view = smoother.push(0);
    expect(view.state).toBe('silent');
    for (let i = 0; i < 40; i += 1) view = smoother.push(0.063);
    expect(view.state).toBe('good');
  });

  it('reset で下限へ戻る', () => {
    const smoother = createLevelSmoother();
    for (let i = 0; i < 40; i += 1) smoother.push(0.5);
    smoother.reset();
    expect(smoother.dbfs).toBe(METER_FLOOR_DBFS);
  });

  it('単発のピークで loud に振れない', () => {
    const smoother = createLevelSmoother();
    for (let i = 0; i < 40; i += 1) smoother.push(0.063);
    const view = smoother.push(1.0);
    expect(view.state).not.toBe('loud');
  });
});
