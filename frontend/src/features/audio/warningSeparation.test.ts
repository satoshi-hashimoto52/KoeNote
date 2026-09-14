/**
 * 「入力なし / 低音量 / 音割れ / Backend停止」を混同しないことの回帰（0019）。
 *
 * 低音量警告（本モジュール）と無進捗ウォッチドッグ（0011）は別系統であり、
 * 片方が他方の事象へ反応してはいけない。反応すると、ユーザーに出る文言が
 * 原因と食い違い、切り分けができなくなる。
 */
import { describe, expect, it } from 'vitest';

import {
  WARNING_HEADINGS,
  WARNING_MESSAGES,
  beginWarning,
  updateWarning,
  type InputLevelState
} from './lowVolumeWarning';
import {
  NO_MARK,
  beginSession,
  isTranscriptionStalled,
  updateProcessedMark
} from '../transcription/watchdog';

const THRESHOLD = 20_000;

function raise(state: InputLevelState) {
  let warning = beginWarning(0);
  warning = updateWarning(warning, state, 0, THRESHOLD).next;
  return updateWarning(warning, state, THRESHOLD, THRESHOLD);
}

describe('4 つの状態が別々の文言になる', () => {
  it('見出しがすべて異なる', () => {
    const headings = Object.values(WARNING_HEADINGS);
    expect(new Set(headings).size).toBe(headings.length);
  });

  it('本文がすべて異なる', () => {
    const messages = Object.values(WARNING_MESSAGES);
    expect(new Set(messages).size).toBe(messages.length);
  });

  it('入力なしはマイクの接続・権限を案内する', () => {
    expect(WARNING_MESSAGES.no_input).toMatch(/接続|権限/);
    expect(WARNING_MESSAGES.no_input).not.toMatch(/音割れ|大きすぎ/);
  });

  it('低音量はマイク位置と入力テストを案内する', () => {
    expect(WARNING_MESSAGES.too_quiet).toMatch(/入力テスト/);
    expect(WARNING_MESSAGES.too_quiet).not.toMatch(/音割れ/);
  });

  it('音割れは音量を下げる案内をする', () => {
    expect(WARNING_MESSAGES.clipping).toMatch(/離す|下げ/);
    expect(WARNING_MESSAGES.clipping).not.toMatch(/権限/);
  });

  it('環境音は騒音源を案内する', () => {
    expect(WARNING_MESSAGES.low_snr).toMatch(/環境音|騒音/);
  });

  it('Backend 停止を示す文言をどれも含まない', () => {
    for (const message of Object.values(WARNING_MESSAGES)) {
      expect(message).not.toMatch(/Backend|バックエンド|停止しました|再起動/);
    }
  });
});

describe('低音量警告とウォッチドッグは干渉しない', () => {
  it('入力レベルの異常ではウォッチドッグが発火しない', () => {
    // 音量が低いだけで文字起こしは進んでいる状況。
    let mark = beginSession('s1', 0);
    for (const [seconds, now] of [[10, 5_000], [20, 10_000], [30, 15_000]] as const) {
      mark = updateProcessedMark(mark, 's1', seconds, now);
    }
    expect(isTranscriptionStalled(mark, 30_000, THRESHOLD)).toBe(false);

    // 同じ状況で低音量警告は出る。
    expect(raise('too_quiet').raise).toBe('too_quiet');
  });

  it('文字起こしが止まっても低音量警告は出ない', () => {
    // 進捗が止まった = ウォッチドッグの担当。
    let mark = beginSession('s1', 0);
    mark = updateProcessedMark(mark, 's1', 10, 1_000);
    expect(isTranscriptionStalled(mark, 100_000, THRESHOLD)).toBe(true);

    // 入力レベル自体は正常なので、低音量側は無反応。
    let warning = beginWarning(0);
    for (const now of [0, 20_000, 100_000]) {
      const decision = updateWarning(warning, 'ok', now, THRESHOLD);
      warning = decision.next;
      expect(decision.raise).toBeNull();
    }
  });

  it('ウォッチドッグは未観測なら判定しない（低音量側も同じ方針）', () => {
    expect(isTranscriptionStalled(NO_MARK, 999_999, THRESHOLD)).toBe(false);
    const warning = beginWarning(0);
    expect(updateWarning(warning, 'ok', 999_999, THRESHOLD).raise).toBeNull();
  });

  it('無言時間はどちらの系統でも異常にならない', () => {
    // 発話が無いだけなら level は silent_ok、進捗も止まらない。
    let warning = beginWarning(0);
    const decision = updateWarning(warning, 'silent_ok', 600_000, THRESHOLD);
    expect(decision.raise).toBeNull();

    let mark = beginSession('s1', 0);
    mark = updateProcessedMark(mark, 's1', 600, 600_000);
    expect(isTranscriptionStalled(mark, 601_000, THRESHOLD)).toBe(false);
  });
});
