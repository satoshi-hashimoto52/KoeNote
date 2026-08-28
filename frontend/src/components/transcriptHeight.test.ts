import { describe, it, expect } from 'vitest';
import {
  DEFAULT_TRANSCRIPT_HEIGHT,
  MAX_TRANSCRIPT_HEIGHT,
  MIN_TRANSCRIPT_HEIGHT,
  SAVE_DEBOUNCE_MS,
  TRANSCRIPT_HEIGHT_KEY,
  VIEWPORT_RATIO,
  normalizeTranscriptHeight,
  shouldPersistHeight
} from './transcriptHeight';

describe('0008: 採用値', () => {
  it('保存キー・初期値・最小・最大が仕様どおり', () => {
    expect(TRANSCRIPT_HEIGHT_KEY).toBe('transcriptHeight');
    expect(DEFAULT_TRANSCRIPT_HEIGHT).toBe(320);
    expect(MIN_TRANSCRIPT_HEIGHT).toBe(180);
    expect(MAX_TRANSCRIPT_HEIGHT).toBe(1200);
    expect(VIEWPORT_RATIO).toBe(0.7);
    expect(SAVE_DEBOUNCE_MS).toBe(500);
  });
});

describe('0008: normalizeTranscriptHeight のクランプ', () => {
  it('未設定・null・undefined は既定値', () => {
    expect(normalizeTranscriptHeight(undefined)).toBe(320);
    expect(normalizeTranscriptHeight(null)).toBe(320);
  });

  it('数値でない値は既定値', () => {
    expect(normalizeTranscriptHeight('abc')).toBe(320);
    expect(normalizeTranscriptHeight({})).toBe(320);
    expect(normalizeTranscriptHeight(NaN)).toBe(320);
    expect(normalizeTranscriptHeight(Infinity)).toBe(320);
  });

  it('0 以下は既定値', () => {
    expect(normalizeTranscriptHeight(0)).toBe(320);
    expect(normalizeTranscriptHeight(-500)).toBe(320);
  });

  it('最小値未満は最小値へクランプ', () => {
    expect(normalizeTranscriptHeight(10)).toBe(180);
    expect(normalizeTranscriptHeight(179)).toBe(180);
  });

  it('最大値超過は最大値へクランプ', () => {
    expect(normalizeTranscriptHeight(99999)).toBe(1200);
    expect(normalizeTranscriptHeight(1201)).toBe(1200);
  });

  it('範囲内はそのまま（整数化）', () => {
    expect(normalizeTranscriptHeight(400)).toBe(400);
    expect(normalizeTranscriptHeight(400.4)).toBe(400);
  });

  it('数値文字列も受け付ける（古い設定の後方互換）', () => {
    expect(normalizeTranscriptHeight('450')).toBe(450);
  });
});

describe('0008: 画面内に収めることを保存値より優先する', () => {
  it('小さいウィンドウでは viewport の 70% までに抑える', () => {
    // 600px の 70% = 420
    expect(normalizeTranscriptHeight(1000, 600)).toBe(420);
  });

  it('保存値が viewport 上限より小さければそのまま', () => {
    expect(normalizeTranscriptHeight(300, 1000)).toBe(300);
  });

  it('極端に小さいウィンドウでも最小高さは割らない', () => {
    // 200px の 70% = 140 だが、最小 180 を優先する
    expect(normalizeTranscriptHeight(500, 200)).toBe(MIN_TRANSCRIPT_HEIGHT);
  });

  it('viewport が未指定・0・負なら適用しない', () => {
    expect(normalizeTranscriptHeight(1000, undefined)).toBe(1000);
    expect(normalizeTranscriptHeight(1000, 0)).toBe(1000);
    expect(normalizeTranscriptHeight(1000, -1)).toBe(1000);
  });

  it('大きいウィンドウでも最大値は超えない', () => {
    expect(normalizeTranscriptHeight(5000, 4000)).toBe(1200);
  });
});

describe('0008: 保存回数の抑制', () => {
  it('1px 未満の変化では保存しない', () => {
    expect(shouldPersistHeight(300, 300)).toBe(false);
    expect(shouldPersistHeight(300, 300.4)).toBe(false);
  });

  it('1px 以上の変化なら保存する', () => {
    expect(shouldPersistHeight(300, 301)).toBe(true);
    expect(shouldPersistHeight(300, 299)).toBe(true);
  });
});

describe('0008: 自動スクロールの追従判定（既存動作を壊さない）', () => {
  /** TranscriptView の onScroll と同じ式。 */
  const nearBottom = (scrollHeight: number, scrollTop: number, clientHeight: number) =>
    scrollHeight - scrollTop - clientHeight < 40;

  it('末尾付近なら追従する', () => {
    expect(nearBottom(1000, 960, 40)).toBe(true);
    expect(nearBottom(1000, 961, 40)).toBe(true);
  });

  it('上へスクロールしたら追従を止める', () => {
    expect(nearBottom(1000, 500, 40)).toBe(false);
  });

  it('高さを変えても判定式は clientHeight を通じて追従する', () => {
    // 欄を高くする（clientHeight 増）と、同じ scrollTop でも末尾付近になりうる
    expect(nearBottom(1000, 700, 200)).toBe(false);
    expect(nearBottom(1000, 700, 280)).toBe(true);
  });
});
