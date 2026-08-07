import { useLayoutEffect, useRef, useState } from 'react';

interface Props {
  committed: string;
  partial: string;
}

/**
 * 確定全文(committed) + 認識中(partial) を表示する。
 * 自動スクロールし、ユーザーが上へスクロールしたら追従を止める。
 */
export function TranscriptView({ committed, partial }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);

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

  const jumpToLatest = () => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      setAutoFollow(true);
    }
  };

  const empty = !committed && !partial;

  return (
    <div className="transcript">
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
