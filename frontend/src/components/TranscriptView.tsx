import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  MAX_TRANSCRIPT_HEIGHT,
  MIN_TRANSCRIPT_HEIGHT,
  SAVE_DEBOUNCE_MS,
  normalizeTranscriptHeight,
  shouldPersistHeight
} from './transcriptHeight';

interface Props {
  committed: string;
  partial: string;
  /** 設定から復元した高さ。未設定なら既定値を使う。 */
  savedHeight?: number | null;
  /** ドラッグ完了後に呼ばれる。連続書き込みはこの中で起きない。 */
  onHeightChange?: (height: number) => void;
}

/**
 * 確定全文(committed) + 認識中(partial) を表示する。
 * 自動スクロールし、ユーザーが上へスクロールしたら追従を止める。
 * 下端ドラッグで縦方向に伸縮できる（0008）。
 */
export function TranscriptView({ committed, partial, savedHeight, onHeightChange }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const [height, setHeight] = useState(() =>
    normalizeTranscriptHeight(savedHeight, typeof window !== 'undefined' ? window.innerHeight : undefined)
  );
  const saveTimerRef = useRef<number | null>(null);
  const lastSavedRef = useRef(height);

  // 設定の読み込みが後から届く場合に追従する。
  useEffect(() => {
    if (savedHeight === undefined || savedHeight === null) return;
    const next = normalizeTranscriptHeight(savedHeight, window.innerHeight);
    setHeight(next);
    lastSavedRef.current = next;
  }, [savedHeight]);

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

  const persist = useCallback(
    (next: number) => {
      if (!onHeightChange || !shouldPersistHeight(lastSavedRef.current, next)) return;
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
      // ドラッグ中は何度も発火するので、落ち着いてから 1 回だけ書く。
      saveTimerRef.current = window.setTimeout(() => {
        saveTimerRef.current = null;
        lastSavedRef.current = next;
        onHeightChange(next);
      }, SAVE_DEBOUNCE_MS);
    },
    [onHeightChange]
  );

  // resize: vertical による高さ変更を拾う。
  useEffect(() => {
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0]?.contentRect.height ?? 0);
      if (next <= 0) return;
      persist(next);
      // 伸縮後も末尾に追従しているなら、末尾へ寄せ直す。
      const el = scrollRef.current;
      if (el && autoFollow) el.scrollTop = el.scrollHeight;
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, [persist, autoFollow]);

  useEffect(
    () => () => {
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    },
    []
  );

  const jumpToLatest = () => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      setAutoFollow(true);
    }
  };

  const empty = !committed && !partial;

  return (
    <div
      className="transcript"
      ref={boxRef}
      style={{ height, minHeight: MIN_TRANSCRIPT_HEIGHT, maxHeight: MAX_TRANSCRIPT_HEIGHT }}
    >
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
  );
}
