// レスポンシブ検証用の使い捨てスクリプト。
// 本番の Chromium(Electron) で built ページを各幅で読み込み、横スクロール発生有無を測定する。
// アプリのロジックには一切関与しない（表示レイアウトの計測のみ）。
const { app, BrowserWindow } = require('electron');
const path = require('path');

const WIDTHS = [960, 760, 600, 500, 460];
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
      const row3 = document.querySelector('.row-3');
      const cols = row3 ? getComputedStyle(row3).gridTemplateColumns.split(' ').filter(Boolean).length : null;
      const tr = document.querySelector('.transcript');
      return {
        innerWidth: window.innerWidth,
        docScrollWidth: de.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        hasHScroll: de.scrollWidth > window.innerWidth + 1,
        settingsCols: cols,
        transcriptH: tr ? Math.round(tr.getBoundingClientRect().height) : null
      };
    })()`);
    results.push({ requested: w, ...m });
  }

  for (const r of results) {
    console.log(
      `width=${r.requested} innerWidth=${r.innerWidth} docScrollWidth=${r.docScrollWidth} ` +
      `hScroll=${r.hasHScroll ? 'YES(!!)' : 'no'} settingsCols=${r.settingsCols} transcriptH=${r.transcriptH}px`
    );
  }
  const anyOverflow = results.some((r) => r.hasHScroll);
  console.log(anyOverflow ? 'RESULT: HORIZONTAL OVERFLOW DETECTED' : 'RESULT: no horizontal overflow at any width');
  app.exit(anyOverflow ? 1 : 0);
});
