import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  MAX_TRANSCRIPT_HEIGHT,
  MIN_TRANSCRIPT_HEIGHT,
  availableHeightFor,
  computeEffectiveHeight,
  heightFromDrag,
  normalizePreferredHeight,
  shouldPersistHeight
} from './transcriptHeight';

interface Props {
  committed: string;
  partial: string;
  /** 設定から復元した「ユーザーの希望高さ」。未設定なら既定値。 */
  preferredHeight?: number | null;
  /** ユーザー操作で希望高さが決まったときだけ呼ばれる。レイアウト変化では呼ばれない。 */
  onPreferredHeightChange?: (height: number) => void;
}

/** キーボード操作 1 回あたりの変化量。 */
const KEY_STEP = 24;

/**
 * 確定全文(committed) + 認識中(partial) を表示する。
 * 自動スクロールし、ユーザーが上へスクロールしたら追従を止める。
 *
 * 高さは専用ハンドルのドラッグで変える（0008）。
 * CSS の `resize: vertical` + ResizeObserver は使わない。
 * ResizeObserver は内容ボックス（ボーダーを除いた高さ）を返すため、
 * その値を保存すると 2px ずつ縮み続けるフィードバックループになる。
 */
export function TranscriptView({
  committed,
  partial,
  preferredHeight,
  onPreferredHeightChange
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);

  // ユーザーの希望高さ。ウィンドウ変化では動かさない。
  const [preferred, setPreferred] = useState(() => normalizePreferredHeight(preferredHeight));
  // ドラッグ中の一時的な表示高さ。確定するまで保存しない。
  const [dragging, setDragging] = useState<number | null>(null);
  const [available, setAvailable] = useState(() =>
    availableHeightFor(typeof window !== 'undefined' ? window.innerHeight : undefined)
  );
  const dragStartRef = useRef<{ y: number; height: number; pointerId: number } | null>(null);

  // 設定の読み込みが後から届く場合に追従する。
  useEffect(() => {
    if (preferredHeight === undefined || preferredHeight === null) return;
    setPreferred(normalizePreferredHeight(preferredHeight));
  }, [preferredHeight]);

  // ウィンドウの縦方向の伸縮に追従する。preferred は変えない。
  useEffect(() => {
    const onResize = () => setAvailable(availableHeightFor(window.innerHeight));
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const effective = computeEffectiveHeight(dragging ?? preferred, available);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoFollow(nearBottom);
  };

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && autoFollow) {
      el.scrollTop = el.scrollHeight;
    }
  }, [committed, partial, autoFollow]);

  // 高さが変わったときも、追従中なら末尾へ寄せ直す。
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && autoFollow) el.scrollTop = el.scrollHeight;
  }, [effective, autoFollow]);

  const commitPreferred = useCallback(
    (next: number) => {
      const normalized = normalizePreferredHeight(next);
      setPreferred((prev) => {
        if (onPreferredHeightChange && shouldPersistHeight(prev, normalized)) {
          onPreferredHeightChange(normalized);
        }
        return normalized;
      });
    },
    [onPreferredHeightChange]
  );

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragStartRef.current = { y: event.clientY, height: effective, pointerId: event.pointerId };
    setDragging(effective);
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStartRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    // 縦方向だけ見る。横移動は無視する。
    setDragging(heightFromDrag(start.height, event.clientY - start.y));
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStartRef.current;
    if (!start || start.pointerId !== event.pointerId) return;
    dragStartRef.current = null;
    const next = dragging;
    setDragging(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // ドラッグ終了時に 1 回だけ保存する。
    if (next !== null) commitPreferred(next);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      commitPreferred(preferred - KEY_STEP);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      commitPreferred(preferred + KEY_STEP);
    }
  };

  const jumpToLatest = () => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      setAutoFollow(true);
    }
  };

  const empty = !committed && !partial;

  return (
    <div className="transcript-wrap">
      <div className="transcript" style={{ height: effective }}>
        <div className="transcript-scroll" ref={scrollRef} onScroll={onScroll}>
          {empty ? (
            <span className="transcript-empty">ここに文字起こしが表示されます</span>
          ) : (
            <>
              <span className="transcript-committed">{committed}</span>
              {partial ? <span className="transcript-partial"> {partial}</span> : null}
            </>
          )}
        </div>
        {!autoFollow ? (
          <button type="button" className="transcript-jump" onClick={jumpToLatest}>
            ↓ 最新位置へ戻る
          </button>
        ) : null}
      </div>
      <div
        className={`transcript-resizer${dragging !== null ? ' dragging' : ''}`}
        role="separator"
        aria-orientation="horizontal"
        aria-label="文字起こし欄の高さ"
        aria-valuenow={effective}
        aria-valuemin={MIN_TRANSCRIPT_HEIGHT}
        aria-valuemax={MAX_TRANSCRIPT_HEIGHT}
        tabIndex={0}
        title="ドラッグで高さを変更（↑↓キーでも変更できます）"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
      />
    </div>
  );
}
