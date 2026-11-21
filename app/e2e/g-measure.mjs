const { chromium } = await import(process.env.PLAYWRIGHT_MODULE + '/index.mjs');
const BASE = process.env.SAGE_URL;
const keys = {
  '3375:35009': ['PersonRow', 44],
  '3378:33008': ['InlineNotice', 34],
  '3375:41048': ['SidebarWorkspaceItem', 34],
  '3378:31735': ['SidebarWorkspaceItem', 34],
  '3378:34864': ['ConfirmDialog', 200],
};
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.goto(BASE + '/__components?key=' + Object.keys(keys).join('&key='), { waitUntil: 'networkidle' });
await page.waitForTimeout(500);
const out = await page.evaluate((keys) => {
  const res = {};
  for (const el of document.querySelectorAll('[data-figma-key]')) {
    const k = el.getAttribute('data-figma-key');
    const child = el.firstElementChild;
    res[k] = child ? Math.round(child.getBoundingClientRect().height * 100) / 100 : null;
  }
  return res;
}, keys);
for (const [k, [name, want]] of Object.entries(keys)) console.log(k, name, 'figma', want, 'code', out[k]);
await browser.close();
