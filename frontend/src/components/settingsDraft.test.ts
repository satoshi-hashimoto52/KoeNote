import { describe, it, expect } from 'vitest';
import {
  type CaptureSettings,
  applyPickedFolder,
  commitDraft,
  createDraft,
  discardDraft,
  hasChanges,
  hasErrors,
  normalizeGptUrl,
  updateDraft,
  validateDraft
} from './settingsDraft';

const current: CaptureSettings = {
  gptUrl: 'https://chatgpt.com/g/g-old',
  saveFolder: '/Users/you/Documents/BridgeLog',
  deviceId: 'dev-1',
  model: 'small',
  delayMode: 'balanced'
};

describe('0015: 設定モーダルの下書き', () => {
  it('既存設定値を読み込む', () => {
    expect(createDraft(current)).toEqual(current);
  });

  it('下書きの変更は元の値へ影響しない', () => {
    const draft = updateDraft(createDraft(current), 'model', 'medium');
    expect(draft.model).toBe('medium');
    expect(current.model).toBe('small');
  });

  it('保存すると下書きが確定する', () => {
    const draft = updateDraft(createDraft(current), 'delayMode', 'accuracy');
    expect(commitDraft(current, draft, false)).toEqual({ ...current, delayMode: 'accuracy' });
  });

  it('キャンセルすると元の値のまま', () => {
    const draft = updateDraft(createDraft(current), 'deviceId', 'dev-2');
    expect(discardDraft(current)).toEqual(current);
    expect(draft.deviceId).toBe('dev-2'); // 破棄されるので反映されない
  });

  it('録音中は保存しても現在値のまま（変更不可）', () => {
    const draft = updateDraft(createDraft(current), 'model', 'medium');
    expect(commitDraft(current, draft, true)).toEqual(current);
  });

  it('変更の有無を判定できる', () => {
    expect(hasChanges(current, createDraft(current))).toBe(false);
    expect(hasChanges(current, updateDraft(createDraft(current), 'model', 'tiny'))).toBe(true);
  });

  it('保存キーは既存の 5 つのまま（形式を変えない）', () => {
    expect(Object.keys(createDraft(current)).sort()).toEqual([
      'delayMode', 'deviceId', 'gptUrl', 'model', 'saveFolder'
    ]);
  });

  it('既存の gptUrl と saveFolder を読み込む', () => {
    const d = createDraft(current);
    expect(d.gptUrl).toBe('https://chatgpt.com/g/g-old');
    expect(d.saveFolder).toBe('/Users/you/Documents/BridgeLog');
  });

  it('URL を編集しただけでは元設定を変えない', () => {
    const d = updateDraft(createDraft(current), 'gptUrl', 'https://chatgpt.com/g/g-new');
    expect(d.gptUrl).toBe('https://chatgpt.com/g/g-new');
    expect(current.gptUrl).toBe('https://chatgpt.com/g/g-old');
  });

  it('保存先を選択しただけでは元設定を変えない', () => {
    const d = applyPickedFolder(createDraft(current), '/tmp/new');
    expect(d.saveFolder).toBe('/tmp/new');
    expect(current.saveFolder).toBe('/Users/you/Documents/BridgeLog');
  });

  it('フォルダ選択をキャンセルしたら下書きを変えない', () => {
    const d = createDraft(current);
    expect(applyPickedFolder(d, null)).toBe(d);
    expect(applyPickedFolder(d, '')).toBe(d);
  });

  it('保存で 5 項目をまとめて反映する', () => {
    let d = createDraft(current);
    d = updateDraft(d, 'gptUrl', '  https://chatgpt.com/g/g-new  ');
    d = updateDraft(d, 'saveFolder', ' /tmp/out ');
    d = updateDraft(d, 'deviceId', 'dev-2');
    d = updateDraft(d, 'model', 'medium');
    d = updateDraft(d, 'delayMode', 'accuracy');
    expect(commitDraft(current, d, false)).toEqual({
      gptUrl: 'https://chatgpt.com/g/g-new',
      saveFolder: '/tmp/out',
      deviceId: 'dev-2',
      model: 'medium',
      delayMode: 'accuracy'
    });
  });

  it('保存時に前後の空白を落とす', () => {
    expect(normalizeGptUrl('  https://chatgpt.com/x  ')).toBe('https://chatgpt.com/x');
  });

  it('キャンセルで 5 項目すべてを破棄する', () => {
    let d = createDraft(current);
    d = updateDraft(d, 'gptUrl', 'https://chatgpt.com/g/x');
    d = updateDraft(d, 'saveFolder', '/tmp/x');
    d = updateDraft(d, 'model', 'tiny');
    expect(discardDraft(current)).toEqual(current);
  });

  it('録音中は保存しても 5 項目とも現在値のまま', () => {
    let d = createDraft(current);
    d = updateDraft(d, 'saveFolder', '/tmp/x');
    d = updateDraft(d, 'deviceId', 'dev-9');
    expect(commitDraft(current, d, true)).toEqual(current);
  });

  it('変更がなければ hasChanges は false（不要な書き込みをしない）', () => {
    expect(hasChanges(current, createDraft(current))).toBe(false);
    // 前後空白だけの差は変更とみなさない
    const spaced = updateDraft(createDraft(current), 'gptUrl', '  https://chatgpt.com/g/g-old  ');
    expect(hasChanges(current, spaced)).toBe(false);
  });
});

describe('0015: 設定の検証', () => {
  const ok: CaptureSettings = {
    gptUrl: 'https://chatgpt.com/g/g-x',
    saveFolder: '/tmp/out',
    deviceId: '',
    model: 'small',
    delayMode: 'balanced'
  };

  it('正しい値ならエラーなし', () => {
    const e = validateDraft(ok, true);
    expect(hasErrors(e)).toBe(false);
  });

  it('空 URL を拒否する', () => {
    expect(validateDraft({ ...ok, gptUrl: '' }, true).gptUrl).toBeTruthy();
    expect(validateDraft({ ...ok, gptUrl: '   ' }, true).gptUrl).toBeTruthy();
  });

  it('不正 URL・許可外ドメインを拒否する', () => {
    expect(validateDraft({ ...ok, gptUrl: 'not a url' }, true).gptUrl).toBeTruthy();
    expect(validateDraft({ ...ok, gptUrl: 'https://evil.com/x' }, true).gptUrl).toBeTruthy();
    expect(validateDraft({ ...ok, gptUrl: 'http://chatgpt.com/x' }, true).gptUrl).toBeTruthy();
  });

  it('chat.openai.com も許可する（既存の許可ドメインを維持）', () => {
    expect(validateDraft({ ...ok, gptUrl: 'https://chat.openai.com/g/x' }, true).gptUrl).toBeUndefined();
  });

  it('空の保存先を拒否する', () => {
    expect(validateDraft({ ...ok, saveFolder: '' }, true).saveFolder).toBeTruthy();
    expect(validateDraft({ ...ok, saveFolder: '  ' }, true).saveFolder).toBeTruthy();
  });

  it('存在しない保存先を拒否する', () => {
    expect(validateDraft(ok, false).saveFolder).toBe('保存先が見つかりません');
  });

  it('存在を確認できないときは保留する（エラーにしない）', () => {
    expect(validateDraft(ok, undefined).saveFolder).toBeUndefined();
  });

  it('検証エラーがあれば保存しない', () => {
    const bad = { ...ok, gptUrl: 'https://evil.com/x' };
    const e = validateDraft(bad, true);
    expect(commitDraft(ok, bad, false, e)).toEqual(ok);
  });
});
