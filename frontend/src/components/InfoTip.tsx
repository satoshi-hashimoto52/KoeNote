/**
 * 項目の隣に置く小さな説明マーク（0017）。
 *
 * ブラウザ既定の `title` ツールチップはカーソル位置に出るため、
 * 狭いウィンドウでは本体の文字情報へ大きく被る。
 * ここでは hover / フォーカス時だけ、ステータスカードの**外側（上）**へ
 * カード幅いっぱいに表示し、本体の情報を隠さず画面外へもはみ出さないようにする。
 */
export function InfoTip({ text }: { text: string }): JSX.Element {
  return (
    <span className="infotip">
      {/* マーク自体が読み上げ対象。本文は視覚的な補助なので aria-hidden。 */}
      <span className="infotip-mark" tabIndex={0} role="img" aria-label={text}>
        i
      </span>
      <span className="infotip-body" aria-hidden="true">
        {text}
      </span>
    </span>
  );
}
