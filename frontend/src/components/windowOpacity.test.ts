import { describe, it, expect } from 'vitest';
import {
  DEFAULT_WINDOW_OPACITY,
  MAX_WINDOW_OPACITY,
  MIN_WINDOW_OPACITY,
  WINDOW_OPACITY_KEY,
  WINDOW_OPACITY_STEP,
  normalizeWindowOpacity,
  opacityToPercent,
  percentToOpacity,
  readWindowOpacity
} from './windowOpacity';

describe('定数', () => {
  it('範囲は 0.70〜1.00、既定 1.00、刻み 0.05', () => {
    expect(MIN_WINDOW_OPACITY).toBe(0.7);
    expect(MAX_WINDOW_OPACITY).toBe(1);
    expect(DEFAULT_WINDOW_OPACITY).toBe(1);
    expect(WINDOW_OPACITY_STEP).toBe(0.05);
    expect(WINDOW_OPACITY_KEY).toBe('windowOpacity');
  });
});

describe('normalizeWindowOpacity', () => {
  it('範囲内はそのまま', () => {
    expect(normalizeWindowOpacity(0.7)).toBe(0.7);
    expect(normalizeWindowOpacity(0.85)).toBe(0.85);
    expect(normalizeWindowOpacity(1)).toBe(1);
  });

  it('下限より小さければ 0.70 へ clamp', () => {
    expect(normalizeWindowOpacity(0.69)).toBe(0.7);
    expect(normalizeWindowOpacity(0)).toBe(0.7);
    expect(normalizeWindowOpacity(-5)).toBe(0.7);
  });

  it('上限より大きければ 1.00 へ clamp', () => {
    expect(normalizeWindowOpacity(1.01)).toBe(1);
    expect(normalizeWindowOpacity(42)).toBe(1);
  });

  it('NaN / Infinity は既定へ', () => {
    expect(normalizeWindowOpacity(NaN)).toBe(DEFAULT_WINDOW_OPACITY);
    expect(normalizeWindowOpacity(Infinity)).toBe(DEFAULT_WINDOW_OPACITY);
    expect(normalizeWindowOpacity(-Infinity)).toBe(DEFAULT_WINDOW_OPACITY);
  });

  it('文字列は数字に見えても拒否して既定へ', () => {
    expect(normalizeWindowOpacity('0.8')).toBe(DEFAULT_WINDOW_OPACITY);
    expect(normalizeWindowOpacity('abc')).toBe(DEFAULT_WINDOW_OPACITY);
    expect(normalizeWindowOpacity('')).toBe(DEFAULT_WINDOW_OPACITY);
  });

  it('number 以外は既定へ', () => {
    for (const v of [null, undefined, {}, [], true, false, () => 0.8]) {
      expect(normalizeWindowOpacity(v)).toBe(DEFAULT_WINDOW_OPACITY);
    }
  });

  it('刻みに丸める（小数誤差を持ち込まない）', () => {
    expect(normalizeWindowOpacity(0.8000000001)).toBe(0.8);
    expect(normalizeWindowOpacity(0.849)).toBe(0.85);
  });
});

describe('readWindowOpacity', () => {
  it('未設定なら 1.00', () => {
    expect(readWindowOpacity({})).toBe(1);
    expect(readWindowOpacity({ model: 'small' })).toBe(1);
  });

  it('壊れた設定でも 1.00', () => {
    for (const v of [null, undefined, 'x', 7, []]) {
      expect(readWindowOpacity(v)).toBe(1);
    }
  });

  it('保存値を読む', () => {
    expect(readWindowOpacity({ windowOpacity: 0.8 })).toBe(0.8);
  });

  it('範囲外の保存値は clamp する', () => {
    expect(readWindowOpacity({ windowOpacity: 0.5 })).toBe(0.7);
    expect(readWindowOpacity({ windowOpacity: 2 })).toBe(1);
  });

  it('不正な保存値は 1.00 にして起動を壊さない', () => {
    expect(readWindowOpacity({ windowOpacity: 'abc' })).toBe(1);
    expect(readWindowOpacity({ windowOpacity: NaN })).toBe(1);
  });

  it('既存の 7 項目を壊さない（読むだけ）', () => {
    const settings = {
      gptUrl: 'https://chatgpt.com/g/g-x', saveFolder: '/tmp', deviceId: 'd',
      deviceLabel: 'マイク', model: 'small', delayMode: 'balanced',
      requestTemplate: 't', transcriptHeight: 178
    };
    const copy = { ...settings };
    expect(readWindowOpacity(settings)).toBe(1);
    expect(settings).toEqual(copy);
  });
});

describe('パーセント表示', () => {
  it('0.85 は 85%', () => {
    expect(opacityToPercent(0.85)).toBe(85);
    expect(opacityToPercent(0.7)).toBe(70);
    expect(opacityToPercent(1)).toBe(100);
  });

  it('丸め誤差を出さない', () => {
    expect(opacityToPercent(0.7000000000000001)).toBe(70);
    expect(opacityToPercent(0.8999999999999999)).toBe(90);
  });

  it('percent からの復元', () => {
    expect(percentToOpacity(85)).toBe(0.85);
    expect(percentToOpacity(70)).toBe(0.7);
    expect(percentToOpacity(100)).toBe(1);
  });

  it('percent の不正値も安全に扱う', () => {
    expect(percentToOpacity(NaN)).toBe(DEFAULT_WINDOW_OPACITY);
    expect(percentToOpacity(0)).toBe(0.7);
    expect(percentToOpacity(500)).toBe(1);
  });
});
