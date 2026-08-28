// レスポンシブ検証用の使い捨てスクリプト。
// 本番の Chromium(Electron) で built ページを各幅で読み込み、横スクロール発生有無を測定する。
// アプリのロジックには一切関与しない（表示レイアウトの計測のみ）。
const { app, BrowserWindow } = require('electron');
const path = require('path');

const WIDTHS = [960, 460, 400, 360, 320, 280];
const HEIGHT = 820;
const INDEX = path.join(__dirname, '..', 'frontend', 'dist', 'index.html');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: WIDTHS[0],
    height: HEIGHT,
    show: false,
    webPreferences: { offscreen: true }
  });
  await win.loadFile(INDEX);
  await sleep(600); // React マウント待ち

  const results = [];
  for (const w of WIDTHS) {
    win.setContentSize(w, HEIGHT);
    await sleep(350);
    const m = await win.webContents.executeJavaScript(`(() => {
      const de = document.documentElement;
      // 0015 で設定 3 項目は設定モーダルへ移動。操作行のカラム数を見る。
      const am = document.querySelector('.actions-main');
      const cols = am ? getComputedStyle(am).gridTemplateColumns.split(' ').filter(Boolean).length : null;
      const tr = document.querySelector('.transcript');
      // 主要要素が画面外へ出ていないか
      const overflowing = [];
      document.querySelectorAll('button, input, select, .statusbar, .diag, .transcript').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1)) {
          overflowing.push((el.className || el.tagName) + ':' + Math.round(r.right));
        }
      });
      // ボタンの折り返し具合（1文字ずつ折り返されていないか）
      const btns = [...document.querySelectorAll('.btn-primary, .btn-ghost, .btn-accent, .btn-danger')];
      const narrow = btns.filter((b) => b.getBoundingClientRect().width < 40).map((b) => b.textContent.trim());
      // 主要操作ボタンが存在し、クリック可能な大きさか
      // 0015 で開始／停止は 1 ボタンへ統合された。
      const labels = ['開始', 'クリア', 'マイGPT'];
      const mainBtns = labels.map((t) => {
        const el = btns.find((b) => b.textContent.trim() === t);
        if (!el) return t + ':MISSING';
        const r = el.getBoundingClientRect();
        const ok = r.width >= 44 && r.height >= 24 && r.right <= window.innerWidth + 1 && r.left >= -1;
        return t + ':' + (ok ? 'ok' : 'NG(w=' + Math.round(r.width) + ',right=' + Math.round(r.right) + ')');
      });
      // 要素の重なり（statusbar と actions）
      const sb = document.querySelector('.statusbar');
      const ac = document.querySelector('.actions-main');
      let overlap = 'n/a';
      if (sb && ac) {
        const a = sb.getBoundingClientRect(), b = ac.getBoundingClientRect();
        overlap = a.bottom <= b.top + 1 ? 'no' : 'OVERLAP(!!)';
      }
      return {
        innerWidth: window.innerWidth,
        docScrollWidth: de.scrollWidth,
        hasHScroll: de.scrollWidth > window.innerWidth + 1,
        settingsCols: cols,
        transcriptH: tr ? Math.round(tr.getBoundingClientRect().height) : null,
        overflowing: overflowing.slice(0, 5),
        narrowButtons: narrow.slice(0, 5),
        mainBtns,
        overlap,
        contentScrollable: (() => {
          const c = document.querySelector('.content');
          return c ? c.scrollHeight > c.clientHeight : false;
        })(),
        // 0015: ヘッダーの重なりと 1 行化
        header: (() => {
          const b = document.querySelector('.brand');
          const g = document.querySelector('.gear');
          const l = document.querySelector('.titlebar-lead');
          if (!b || !g || !l) return 'missing';
          const br = b.getBoundingClientRect(), gr = g.getBoundingClientRect(), lr = l.getBoundingClientRect();
          const overlapLead = br.left < lr.right - 1;
          const overlapGear = br.right > gr.left + 1;
          const center = Math.round(Math.abs((br.left + br.right) / 2 - window.innerWidth / 2));
          return (overlapLead || overlapGear ? 'OVERLAP(!!)' : 'ok') + ' center=' + center + 'px';
        })(),
        actionsOneRow: (() => {
          const m = document.querySelector('.actions-main');
          if (!m) return false;
          const bs = [...m.querySelectorAll('button')].map((x) => x.getBoundingClientRect());
          return bs.length === 3 && Math.abs(bs[0].top - bs[1].top) < 2 && Math.abs(bs[1].top - bs[2].top) < 2;
        })()
      };
    })()`);
    results.push({ requested: w, ...m });
  }

  for (const r of results) {
    console.log(
      `width=${r.requested} innerWidth=${r.innerWidth} docScrollWidth=${r.docScrollWidth} ` +
      `hScroll=${r.hasHScroll ? 'YES(!!)' : 'no'} cols=${r.settingsCols} trH=${r.transcriptH} ` +
      `overflow=[${r.overflowing.join(', ')}] narrowBtns=[${r.narrowButtons.join(', ')}]\n` +
      `    btns=[${r.mainBtns.join(' | ')}] overlap=${r.overlap} vScroll=${r.contentScrollable}\n` +
      `    header=${r.header} actions1row=${r.actionsOneRow}`
    );
  }
  const anyOverflow = results.some((r) => r.hasHScroll);
  console.log(anyOverflow ? 'RESULT: HORIZONTAL OVERFLOW DETECTED' : 'RESULT: no horizontal overflow at any width');
  app.exit(anyOverflow ? 1 : 0);
});
