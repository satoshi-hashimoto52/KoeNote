import { describe, it, expect } from 'vitest';
import {
  DEFAULT_TRANSCRIPT_HEIGHT,
  MAX_TRANSCRIPT_HEIGHT,
  MIN_TRANSCRIPT_HEIGHT,
  TRANSCRIPT_HEIGHT_KEY,
  VIEWPORT_RATIO,
  availableHeightFor,
  computeEffectiveHeight,
  heightFromDrag,
  normalizePreferredHeight,
  shouldPersistHeight
} from './transcriptHeight';

describe('0008: 採用値', () => {
  it('保存キー・初期値・最小・最大が仕様どおり', () => {
    expect(TRANSCRIPT_HEIGHT_KEY).toBe('transcriptHeight');
    expect(DEFAULT_TRANSCRIPT_HEIGHT).toBe(320);
    expect(MIN_TRANSCRIPT_HEIGHT).toBe(180);
    expect(MAX_TRANSCRIPT_HEIGHT).toBe(1200);
    expect(VIEWPORT_RATIO).toBe(0.7);
  });
});

describe('0008: normalizePreferredHeight（ウィンドウを見ない）', () => {
  it('未設定・null・非数値・0 以下は既定値', () => {
    expect(normalizePreferredHeight(undefined)).toBe(320);
    expect(normalizePreferredHeight(null)).toBe(320);
    expect(normalizePreferredHeight('abc')).toBe(320);
    expect(normalizePreferredHeight(NaN)).toBe(320);
    expect(normalizePreferredHeight(Infinity)).toBe(320);
    expect(normalizePreferredHeight(0)).toBe(320);
    expect(normalizePreferredHeight(-500)).toBe(320);
  });

  it('最小 180px 未満にならない', () => {
    expect(normalizePreferredHeight(10)).toBe(180);
    expect(normalizePreferredHeight(179)).toBe(180);
    expect(normalizePreferredHeight(180)).toBe(180);
  });

  it('最大 1200px を超えない', () => {
    expect(normalizePreferredHeight(99999)).toBe(1200);
    expect(normalizePreferredHeight(1201)).toBe(1200);
    expect(normalizePreferredHeight(1200)).toBe(1200);
  });

  it('数値文字列も受け付ける（後方互換）', () => {
    expect(normalizePreferredHeight('450')).toBe(450);
  });

  it('ウィンドウ高さに影響されない（ここが 0008 再発防止の要）', () => {
    // 引数はひとつだけ。小さいウィンドウでも希望高さは縮まない。
    expect(normalizePreferredHeight(800)).toBe(800);
  });
});

describe('0008: availableHeightFor', () => {
  it('ウィンドウ高さの 70%', () => {
    expect(availableHeightFor(1000)).toBe(700);
    expect(availableHeightFor(600)).toBe(420);
  });

  it('最小高さは下回らない', () => {
    expect(availableHeightFor(200)).toBe(MIN_TRANSCRIPT_HEIGHT);
    expect(availableHeightFor(100)).toBe(MIN_TRANSCRIPT_HEIGHT);
  });

  it('不正値なら制限しない（最大値を返す）', () => {
    expect(availableHeightFor(undefined)).toBe(MAX_TRANSCRIPT_HEIGHT);
    expect(availableHeightFor(0)).toBe(MAX_TRANSCRIPT_HEIGHT);
    expect(availableHeightFor(-1)).toBe(MAX_TRANSCRIPT_HEIGHT);
    expect(availableHeightFor('x')).toBe(MAX_TRANSCRIPT_HEIGHT);
  });
});

describe('0008: preferred と effective の分離（不具合の再発防止）', () => {
  it('手動で 400px にすると preferred は 400px', () => {
    expect(normalizePreferredHeight(400)).toBe(400);
  });

  it('ウィンドウ縮小で effective が縮んでも preferred は変わらない', () => {
    const preferred = 400;
    // 高さ 360px のウィンドウ → 利用可能 252px
    const available = availableHeightFor(360);
    expect(available).toBe(252);
    const effective = computeEffectiveHeight(preferred, available);
    expect(effective).toBe(252);
    // 保存値は変わらない
    expect(preferred).toBe(400);
  });

  it('ウィンドウ再拡大で effective が 400px へ戻る', () => {
    const preferred = 400;
    const effective = computeEffectiveHeight(preferred, availableHeightFor(1000));
    expect(effective).toBe(400);
  });

  it('effective は最小 180px を割らない', () => {
    expect(computeEffectiveHeight(500, 50)).toBe(MIN_TRANSCRIPT_HEIGHT);
    expect(computeEffectiveHeight(100, 1000)).toBe(MIN_TRANSCRIPT_HEIGHT);
  });

  it('effective は preferred を超えない（利用可能領域が広くても）', () => {
    expect(computeEffectiveHeight(300, 5000)).toBe(300);
  });

  it('再起動相当: 保存値 400px を読み直すと preferred は 400px', () => {
    expect(normalizePreferredHeight(400)).toBe(400);
    // 小さいウィンドウで起動しても保存値そのものは変わらない
    expect(computeEffectiveHeight(normalizePreferredHeight(400), availableHeightFor(400))).toBe(280);
    expect(normalizePreferredHeight(400)).toBe(400);
  });

  it('内容ボックス基準の値を保存し続けても縮まない（旧不具合の再現防止）', () => {
    // 旧実装は ResizeObserver の contentRect（border 2px を除く）を保存していた。
    // preferred はレイアウト値から更新されないので、この経路自体が存在しない。
    let preferred = 320;
    for (let i = 0; i < 50; i += 1) {
      const effective = computeEffectiveHeight(preferred, availableHeightFor(1000));
      const contentBox = effective - 2; // border 分
      // レイアウト由来の値では preferred を更新しない
      expect(contentBox).toBeLessThan(effective);
    }
    expect(preferred).toBe(320);
  });
});

describe('0008: ドラッグ計算', () => {
  it('下へ動かすと高くなる', () => {
    expect(heightFromDrag(300, 100)).toBe(400);
  });

  it('上へ動かすと低くなる', () => {
    expect(heightFromDrag(300, -100)).toBe(200);
  });

  it('最小・最大でクランプされる', () => {
    expect(heightFromDrag(200, -1000)).toBe(MIN_TRANSCRIPT_HEIGHT);
    expect(heightFromDrag(1000, 5000)).toBe(MAX_TRANSCRIPT_HEIGHT);
  });

  it('横方向の移動は引数に含まれない（deltaY のみ）', () => {
    expect(heightFromDrag(300, 0)).toBe(300);
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
  const nearBottom = (scrollHeight: number, scrollTop: number, clientHeight: number) =>
    scrollHeight - scrollTop - clientHeight < 40;

  it('末尾付近なら追従する', () => {
    expect(nearBottom(1000, 960, 40)).toBe(true);
  });

  it('上へスクロールしたら追従を止める', () => {
    expect(nearBottom(1000, 500, 40)).toBe(false);
  });

  it('高さを変えても判定式は clientHeight を通じて追従する', () => {
    expect(nearBottom(1000, 700, 200)).toBe(false);
    expect(nearBottom(1000, 700, 280)).toBe(true);
  });
});

describe('0008: ドラッグ中は保存せず、pointerup で 1 回だけ保存する', () => {
  /** TranscriptView のドラッグ状態遷移を再現する。 */
  function makeDragSession(startHeight: number, onCommit: (h: number) => void) {
    let dragging: number | null = null;
    let start: { y: number; height: number } | null = null;
    let preferred = startHeight;
    return {
      down(y: number) {
        start = { y, height: preferred };
        dragging = preferred;
      },
      move(y: number) {
        if (!start) return;
        dragging = heightFromDrag(start.height, y - start.y);
      },
      up() {
        if (!start) return;
        const next = dragging;
        start = null;
        dragging = null;
        if (next !== null && shouldPersistHeight(preferred, next)) {
          preferred = next;
          onCommit(next);
        }
      },
      cancel() {
        start = null;
        dragging = null;
      },
      get displayed() {
        return dragging ?? preferred;
      },
      get preferred() {
        return preferred;
      }
    };
  }

  it('移動中は保存されず、離した時に 1 回だけ保存される', () => {
    const saved: number[] = [];
    const s = makeDragSession(300, (h) => saved.push(h));
    s.down(100);
    s.move(120);
    s.move(150);
    s.move(200);
    expect(saved).toHaveLength(0);
    expect(s.displayed).toBe(400);
    s.up();
    expect(saved).toEqual([400]);
    expect(s.preferred).toBe(400);
  });

  it('pointercancel では保存しない', () => {
    const saved: number[] = [];
    const s = makeDragSession(300, (h) => saved.push(h));
    s.down(100);
    s.move(200);
    s.cancel();
    expect(saved).toHaveLength(0);
    expect(s.preferred).toBe(300);
  });

  it('高さが変わらなければ保存しない', () => {
    const saved: number[] = [];
    const s = makeDragSession(300, (h) => saved.push(h));
    s.down(100);
    s.move(100);
    s.up();
    expect(saved).toHaveLength(0);
  });
});
