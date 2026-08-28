import { describe, it, expect } from 'vitest';
import { buildRequestText } from './api';

describe('0007: 依頼文テンプレート', () => {
  it('既定テンプレートに資料関連の文言と {attachment_names} が残っていない', () => {
    const text = buildRequestText('会議A');
    expect(text).not.toContain('{attachment_names}');
    expect(text).not.toContain('資料');
    expect(text).toContain('会議A');
    expect(text).toContain('・文字起こしテキスト');
  });

  it('保存済みテンプレートを優先する', () => {
    expect(buildRequestText('会議B', '独自本文 {title} です')).toBe('独自本文 会議B です');
  });

  // 旧テンプレート互換
  it('旧テンプレートに残った {attachment_names} を空文字へ置換する', () => {
    const legacy = 'タイトル：\n{title}\n\n添付ファイル：\n・文字起こしテキスト\n{attachment_names}\n\n以上';
    const text = buildRequestText('会議C', legacy);
    expect(text).not.toContain('{attachment_names}');
    expect(text).toContain('会議C');
    expect(text).toContain('・文字起こしテキスト');
    expect(text).toContain('以上');
  });

  it('{attachment_names} が複数あってもすべて置換する', () => {
    const text = buildRequestText('x', '{attachment_names} A {attachment_names} B');
    expect(text).not.toContain('{attachment_names}');
    expect(text).toContain('A');
    expect(text).toContain('B');
  });

  it('空テンプレートや空白のみなら既定を使う', () => {
    expect(buildRequestText('会議D', '')).toContain('会議D');
    expect(buildRequestText('会議D', '   ')).toContain('設定済みの形式');
  });

  it('テンプレート未指定でも動く（後方互換）', () => {
    expect(() => buildRequestText('会議E')).not.toThrow();
  });
});
