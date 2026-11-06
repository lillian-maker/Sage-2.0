// Capture website stills from the live Sage App design HTML (same third-party naming rule as the demo video).
// Usage: SAGE_APP_DESIGN=<sage-all-screens-final.html> PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/capture-stills.cjs <out dir>
// Writes PNG at 2x; convert to WebP into assets/app/ (see README).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const path = require('node:path');

const design = process.env.SAGE_APP_DESIGN, out = process.argv[2];
if (!design || !out) throw new Error('Set SAGE_APP_DESIGN and pass an output directory');
// [file name, screen id, clip within the 1440×900 screen]; clips drop the global sidebar.
const STILLS = [['collaboration-inbox', 'screen-collab', { x: 256, y: 0, width: 1184, height: 560 }]];

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  await page.goto('file://' + path.resolve(design));
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const generic = { 'Jungle Scout': '销量数据连接器', Keepa: '价格数据连接器' };
    for (let n; (n = walker.nextNode());) for (const [name, plain] of Object.entries(generic)) if (n.textContent.includes(name)) n.textContent = n.textContent.replaceAll(name, plain);
  });
  for (const [name, id, clip] of STILLS) {
    await page.locator('#' + id).scrollIntoViewIfNeeded();
    const fresh = await page.locator('#' + id).boundingBox();
    await page.screenshot({ path: path.join(out, name + '.png'), clip: { x: fresh.x + clip.x, y: fresh.y + clip.y, width: clip.width, height: clip.height } });
    console.log(name, clip.width, clip.height);
  }
  await browser.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
