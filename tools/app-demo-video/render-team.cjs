// Record the AI team page demo (multi-specialist orchestration and human review) from the live Sage App design HTML.
// Usage: SAGE_APP_DESIGN=<sage-all-screens-final.html> FFMPEG=<ffmpeg with libx264> PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/render-team.cjs
// Writes assets/video/sage-team-collaboration.mp4 and sage-team-collaboration-poster.jpg.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const design = process.env.SAGE_APP_DESIGN;
const ffmpeg = process.env.FFMPEG || 'ffmpeg';
if (!design) throw new Error('Set SAGE_APP_DESIGN to sage-all-screens-final.html from the Sage App design export');
const FPS = 30, W = 1440, H = 900;
// [screen id, seconds]; chapter order matches the AI team page.
const SCENES = [['screen-plan', 8], ['screen-orchestration', 9], ['screen-parallel', 7], ['screen-collab', 7], ['screen-scope', 8.5], ['screen-artifact-preview', 8]];
const STARTS = SCENES.reduce((a, [, d], i) => (a.push(i ? a[i - 1] + SCENES[i - 1][1] : 0), a), []);
const DURATION = STARTS.at(-1) + SCENES.at(-1)[1];
const POSTER = STARTS[1] + 6.4;

// Runs inside the design page: isolates the six screens into a 1440×900 stage and exposes render(t).
function setup({ scenes, starts }) {
  const FZ = 1440 / 1184, SB = 256;
  const stage = document.createElement('div');
  stage.id = 'stage'; stage.style.cssText = 'position:fixed;left:0;top:0;width:1440px;height:900px;overflow:hidden;background:#fff;z-index:99999';
  document.body.appendChild(stage);
  document.documentElement.style.overflow = 'hidden';
  const css = document.createElement('style');
  css.textContent = `#stage .screen{position:absolute;left:0;top:0;margin:0;border-radius:0;box-shadow:none;transform-origin:0 0;will-change:transform,opacity}
  .rec-cursor{position:absolute;width:22px;height:22px;z-index:50;pointer-events:none;filter:drop-shadow(0 2px 3px #14243a55)}
  .rec-ripple{position:absolute;width:36px;height:36px;margin:-18px 0 0 -18px;border-radius:50%;border:2px solid #396397;z-index:49;pointer-events:none}
  .rec-toast{position:absolute;left:50%;bottom:36px;transform:translateX(-50%);display:flex;gap:16px;align-items:center;padding:11px 18px;border-radius:10px;background:#16283c;color:#fff;font-size:13px;z-index:48;box-shadow:0 12px 32px #16283c40;white-space:nowrap;opacity:0;visibility:hidden}
  .rec-toast u{text-decoration:none;color:#9fc0e6}
  .rec-hl{background:#eaf1f9!important;border-radius:6px;transition:none}
  #stage :is(.solid,.ghost,.textbtn){white-space:nowrap}
  .rec-pressed{filter:brightness(.88);transform:scale(.97)}`;
  document.head.appendChild(css);
  const screens = scenes.map(([id]) => { const s = document.getElementById(id); stage.appendChild(s); return s; });
  [...document.body.children].forEach(el => { if (el !== stage && el.tagName !== 'SCRIPT' && el.tagName !== 'STYLE') el.style.display = 'none'; });
  // The design names a third-party data tool whose integration is not confirmed; use a generic name in public material.
  const walker = document.createTreeWalker(stage, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) if (n.textContent.includes('Jungle Scout')) n.textContent = n.textContent.replaceAll('Jungle Scout', '销量数据连接器');

  const q = (s, sel) => s.querySelector(sel), qa = (s, sel) => [...s.querySelectorAll(sel)];
  const byText = (s, sel, text) => qa(s, sel).find(el => el.textContent.includes(text));
  const center = (s, el) => { const a = s.getBoundingClientRect(), b = el.getBoundingClientRect(); return [b.x - a.x + b.width / 2, b.y - a.y + b.height / 2]; };
  const ease = v => { v = Math.max(0, Math.min(1, v)); return v < .5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2; };
  const mix = (a, b, k) => a + (b - a) * k;
  const fade = (t, at, d = .35) => Math.max(0, Math.min(1, (t - at) / d));
  const show = (el, k, dy = 8) => { if (!el) return; el.style.opacity = k; el.style.transform = k >= 1 ? '' : `translateY(${(1 - k) * dy}px)`; };
  const text = (el, value) => { if (el && el.firstChild && el.dataset.recText !== value) { el.dataset.recText = value; [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).forEach((n, i) => { n.textContent = i ? '' : value; }); } };
  const icons = (() => { const o = document.getElementById('screen-orchestration'); const pick = c => q(o, `.nd.${c} .st img`)?.src; return { done: pick('done'), run: pick('run'), wait: pick('wait') }; })();
  const queueIcon = qa(document.getElementById('screen-orchestration'), '.nd').map(n => q(n, '.st img')?.src).find(src => src && !Object.values(icons).includes(src));

  // Per-scene cursor and toast layers.
  const layers = screens.map(s => {
    const cur = document.createElement('div'); cur.className = 'rec-cursor';
    cur.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.4-6.8L4 20z" fill="#fff" stroke="#16283c" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const rip = document.createElement('div'); rip.className = 'rec-ripple';
    s.append(cur, rip); return { cur, rip };
  });
  const toastEl = document.createElement('div'); toastEl.className = 'rec-toast'; toastEl.style.zIndex = 5; stage.appendChild(toastEl);
  let current = 0, toastShown = false;
  // Cursor path: [[time, x, y, click?], ...] in scene-local seconds and screen pixels.
  const cursor = (i, t, path) => {
    const { cur, rip } = layers[i];
    if (!path.length || t < path[0][0] - .3) { cur.style.opacity = 0; rip.style.opacity = 0; return; }
    let x = path[0][1], y = path[0][2];
    for (let j = 1; j < path.length; j++) { const [t0, x0, y0] = path[j - 1], [t1, x1, y1] = path[j]; if (t >= t0) { const k = ease((t - t0) / Math.max(.01, t1 - t0)); x = mix(x0, x1, k); y = mix(y0, y1, k); } }
    cur.style.opacity = Math.min(1, (t - path[0][0] + .3) / .3); cur.style.left = x - 3 + 'px'; cur.style.top = y - 2 + 'px';
    const click = path.filter(p => p[3]).map(p => p[0]).find(c => t >= c && t < c + .5);
    if (click !== undefined) { const k = (t - click) / .5; rip.style.opacity = 1 - k; rip.style.left = x + 'px'; rip.style.top = y + 'px'; rip.style.transform = `scale(${.4 + k})`; } else rip.style.opacity = 0;
  };
  const toast = (i, t, at, until, html) => { if (i !== current) return; const el = toastEl; toastShown = true; const k = Math.min(fade(t, at, .3), 1 - fade(t, until, .3)); el.style.opacity = k; el.style.visibility = k > 0 ? 'visible' : 'hidden'; if (el.dataset.html !== html) { el.dataset.html = html; el.innerHTML = html; } };
  const press = (el, t, at) => el && el.classList.toggle('rec-pressed', t >= at && t < at + .22);
  // Camera: [time, zoom, left, top] keyframes, left/top in screen pixels.
  const camera = (s, t, keys) => {
    let [, z, x, y] = keys[0];
    for (let j = 1; j < keys.length; j++) { const a = keys[j - 1], b = keys[j]; if (t >= a[0]) { const k = ease((t - a[0]) / Math.max(.01, b[0] - a[0])); z = mix(a[1], b[1], k); x = mix(a[2], b[2], k); y = mix(a[3], b[3], k); } }
    s.style.transform = `translate(${-x * z}px,${-y * z}px) scale(${z})`;
  };
  const focus = (y = 40) => [FZ, SB, y];

  // --- Scene 1: plan confirmation
  const s1 = screens[0];
  const s1msgs = qa(s1, '.msg');
  const s1plan = q(s1, '.ph2').parentElement;
  const s1rows = [...s1plan.children].slice(1, -1).flatMap(c => c.children.length && !c.classList.contains('pstep') && !c.classList.contains('pstage') ? [...c.children] : [c]);
  const s1foot = s1plan.lastElementChild;
  const s1body = q(s1msgs[1], '.body'), s1full = s1body.textContent.trim();
  const s1go = byText(s1, '.solid', '开始执行'), s1pill = q(s1, '.pill.warn');
  const s1goC = center(s1, s1go);
  // --- Scene 2: orchestration
  const s2 = screens[1];
  const nodes = qa(s2, '.nd');
  const nodeState = nodes.map(n => ({ n, icon: q(n, '.st img'), label: q(n, '.m span'), cost: q(n, '.m span:last-child'), base: q(n, '.m span:last-child').textContent }));
  const drawer = q(s2, '.conflict').closest('aside') || q(s2, '.conflict').parentElement;
  const s2head = qa(s2, 'b'), s2progress = s2head.find(b => /\/ 9/.test(b.textContent)), s2credits = s2head.find(b => b.textContent.trim() === '186');
  const s2target = center(s2, nodes[4]);
  nodes[4].classList.remove('sel');
  // --- Scene 3: parallel work
  const s3 = screens[2];
  const rows = qa(s3, '.d9row').map(r => ({ r, icon: q(r, 'img.ic'), dn: q(r, '.dn'), tm: q(r, '.tm'), dn0: q(r, '.dn').textContent, tm0: q(r, '.tm').textContent }));
  const secs = v => { const m = v.match(/(\d+) 分 (\d+) 秒/); return m ? +m[1] * 60 + +m[2] : null; };
  const fmt = v => `${Math.floor(v / 60)} 分 ${String(Math.floor(v % 60)).padStart(2, '0')} 秒`;
  const s3ph = q(s3, '.ph'), s3work = q(s3, '.d9work'), s3stepIcon = qa(s3, '.step img.ic')[0];
  const s3rowC = center(s3, rows[0].r);
  // --- Scene 4: collaboration inbox
  const s4 = screens[3];
  const itemEls = qa(s4, '.k').map(k => { let el = k; while (el.parentElement && el.parentElement.querySelectorAll('.k').length === 1) el = el.parentElement; return el; });
  const activeClass = (() => { const a = itemEls[0].className.split(' '), b = itemEls[1].className.split(' '); return a.filter(c => c && !b.includes(c)); })();
  itemEls[0].classList.remove(...activeClass);
  const detail = q(s4, '.ctx').parentElement;
  const s4approve = byText(detail, '.solid', '批准');
  // --- Scene 5: authorization scope
  const s5 = screens[4];
  const split = q(s5, '.b9split'), menu = q(s5, '.b9scope'), car = q(s5, '.car'), its = qa(menu, '.it'), okBtn = q(split, '.solid');
  const confirmCard = q(s5, '.confirm'), ch = q(confirmCard, '.ch'), acts = q(confirmCard, '.acts'), s5pill = q(s5, '.pill.warn');
  const step4 = qa(s5, '.step')[3], step4small = step4 && q(step4, 'small'), step4icon = step4 && q(step4, 'img.ic'), step4div = step4 && q(step4, '.s');
  const step4icon0 = step4icon && step4icon.src;
  menu.style.transition = 'none';
  const s5carC = center(s5, car), s5okC = center(s5, okBtn), s5itC = its.map(it => center(s5, it));
  menu.style.display = 'none';
  // --- Scene 6: acceptance and versioning
  const s6 = screens[5];
  const cites = qa(s6, '.cite'), arts = qa(s6, '.art'), accept = byText(s6, '.solid', '验收通过'), vsel = q(s6, '.vsel'), ver = q(s6, '.ver-row.on');
  const c1 = center(s6, cites[0]), c3 = center(s6, cites[2]), acceptC = center(s6, accept);

  window.render = T => {
    const idx = Math.max(0, starts.findLastIndex(s => T >= s)); current = idx; toastShown = false;
    screens.forEach((s, i) => { const st = starts[i], en = st + scenes[i][1]; s.style.opacity = i === 0 && T < .01 ? 1 : (T >= st - .4 && T < en ? Math.min(1, (T - st + .4) / .4) : 0); s.style.zIndex = i === idx ? 2 : 1; });
    const lt = i => T - starts[i];
    // Scene 1
    { const t = lt(0);
      camera(s1, t, [[0, 1, 0, 0], [1.6, 1, 0, 0], [3.4, ...focus(70)], [8, ...focus(110)]]);
      show(s1msgs[0], fade(t, .15)); show(s1msgs[1], fade(t, .9));
      const n = Math.round(s1full.length * Math.max(0, Math.min(1, (t - 1.1) / 1))); text(s1body, s1full.slice(0, n) || '\u200b');
      show(s1plan, fade(t, 2.1));
      s1rows.forEach((r, i) => show(r, fade(t, 2.4 + i * .13, .25), 6));
      show(s1foot, fade(t, 2.4 + s1rows.length * .13 + .1));
      cursor(0, t, [[4.9, 1180, 860], [5.9, ...s1goC], [6.15, ...s1goC, 1], [8, s1goC[0] + 40, s1goC[1] + 60]]);
      press(s1go, t, 6.15);
      const started = t >= 6.35; text(s1go, started ? '已开始' : '开始执行'); s1go.style.opacity = started ? .55 : 1;
      text(s1pill, started ? '进行中' : '待你确认计划'); s1pill.classList.toggle('warn', !started);
      toast(0, t, 6.5, 8.4, '计划已确认 · 5 位专家开始工作'); }
    // Scene 2
    { const t = lt(1);
      camera(s2, t, [[0, ...focus(30)], [9, ...focus(46)]]);
      const st = [];
      st[0] = t < .9 ? 'run' : 'done';
      [1, 2, 3].forEach(i => { st[i] = t < 1.3 ? 'queue' : 'run'; });
      if (t >= 2.7) st[1] = 'done'; if (t >= 3.5) st[3] = 'done';
      st[4] = t < 4.2 ? 'queue' : 'wait';
      [5, 6, 7, 8].forEach(i => { st[i] = 'queue'; });
      const label = { done: '已完成', run: '进行中', wait: '待你确认', queue: '排队' };
      nodeState.forEach(({ n, icon, label: l, cost, base }, i) => {
        n.classList.remove('done', 'run', 'wait'); if (st[i] !== 'queue') n.classList.add(st[i]);
        if (icon) { icon.src = st[i] === 'queue' ? queueIcon : icons[st[i]]; icon.style.transform = st[i] === 'run' ? `rotate(${t * 300}deg)` : ''; }
        text(l, label[st[i]]); text(cost, st[i] === 'done' || st[i] === 'run' ? base.replace('约 ', '') : (/\d/.test(base) ? (base.startsWith('约') ? base : '约 ' + base) : base));
      });
      nodes[4].classList.toggle('sel', t >= 4.9);
      nodes[4].style.boxShadow = t >= 4.2 && t < 4.9 ? `0 0 0 ${3 + 3 * Math.sin(t * 9)}px #e8b04a55` : '';
      text(s2progress, `${st.filter(s => s === 'done').length} / 9 步`);
      text(s2credits, String(Math.round(186 * Math.min(1, t / 4.2))));
      const dk = ease(fade(t, 5.05, .45)); drawer.style.opacity = dk; drawer.style.transform = `translateX(${(1 - dk) * 40}px)`;
      cursor(1, t, [[3.9, 760, 640], [4.75, ...s2target], [4.9, ...s2target, 1], [6.2, 1200, 160], [9, 1215, 170]]); }
    // Scene 3
    { const t = lt(2);
      camera(s3, t, [[0, ...focus(60)], [7, ...focus(110)]]);
      rows.forEach(({ r, icon, dn, tm, dn0, tm0 }, i) => {
        const done = i === 0 && t >= 3.6, base = secs(tm0);
        if (base !== null) text(tm, fmt(base + (done ? 3.6 : t) * 4));
        if (icon && i < 3) { icon.src = done ? icons.done : icons.run; icon.style.transform = done ? '' : `rotate(${t * 300}deg)`; }
        text(dn, done ? '竞品格局分析 · 已交给望野汇总' : dn0);
        r.style.opacity = i === 0 && done ? .7 : 1;
      });
      text(s3ph, t >= 3.6 ? '正在等待 2 位专家 · 1 个在后台' : '正在等待 3 位专家 · 1 个在后台');
      text(s3work, `已工作 ${fmt(17 * 60 + 5 + t * 4)}`);
      cursor(2, t, [[4.6, 900, 700], [5.6, s3rowC[0] + 120, s3rowC[1]], [7, s3rowC[0] + 130, s3rowC[1] + 4]]); }
    // Scene 4
    { const t = lt(3);
      camera(s4, t, [[0, ...focus(30)], [7, ...focus(40)]]);
      const hover = t < .8 ? -1 : t < 1.8 ? 1 : t < 2.8 ? 2 : t < 3.6 ? 3 : 0;
      itemEls.forEach((el, i) => { el.classList.toggle('rec-hl', i === hover && !(i === 0 && t >= 4.1)); });
      itemEls[0].classList.toggle(activeClass[0] || 'on', t >= 4.1);
      show(detail, fade(t, 4.2, .4), 10);
      const pts = itemEls.map(el => center(s4, el));
      cursor(3, t, [[.4, 700, 700], [1.1, pts[1][0] + 60, pts[1][1]], [2.1, pts[2][0] + 60, pts[2][1]], [3.1, pts[3][0] + 60, pts[3][1]], [3.9, pts[0][0] + 60, pts[0][1]], [4.05, pts[0][0] + 60, pts[0][1], 1], [5.6, ...center(s4, s4approve)], [7, center(s4, s4approve)[0] + 8, center(s4, s4approve)[1] + 3]]); }
    // Scene 5
    { const t = lt(4);
      camera(s5, t, [[0, ...focus(120)], [8.5, ...focus(150)]]);
      const open = t >= 1.35 && t < 4.15; menu.style.display = open ? '' : 'none';
      const hov = t < 2.1 ? 0 : t < 2.9 ? 1 : t < 3.6 ? 2 : 0;
      its.forEach((it, i) => it.classList.toggle('rec-hl', open && i === hov));
      cursor(4, t, [[.5, 760, 780], [1.2, ...s5carC], [1.35, ...s5carC, 1], [2.1, ...s5itC[1]], [2.9, ...s5itC[2]], [3.7, ...s5itC[0]], [3.95, ...s5itC[0], 1], [4.7, ...s5okC], [4.9, ...s5okC, 1], [8.5, s5okC[0] + 60, s5okC[1] + 80]]);
      press(okBtn, t, 4.9);
      const ok = t >= 5.1;
      acts.style.display = ok ? 'none' : '';
      text(ch, ok ? '已批准 · 仅本次任务 · 只读查询' : '需要你确认'); ch.style.color = ok ? '#2f7a55' : '';
      confirmCard.style.borderColor = ok ? '#b9dcc8' : ''; confirmCard.style.background = ok ? '#f5fbf7' : '';
      text(s5pill, ok ? '进行中' : '待你确认'); s5pill.classList.toggle('warn', !ok);
      if (step4small) text(step4small, ok ? '望野 · 正在估算销量' : '等待你确认数据使用');
      if (step4icon) { step4icon.src = ok ? icons.run : step4icon0; step4icon.style.transform = ok ? `rotate(${t * 300}deg)` : ''; }
      if (step4div) step4div.style.color = ok ? 'var(--text-1, #16283c)' : '';
      toast(4, t, 5.2, 8.6, '已批准 · 仅对这一次查询生效 <u>撤销</u>'); }
    // Scene 6
    { const t = lt(5);
      camera(s6, t, [[0, ...focus(0)], [8, ...focus(0)]]);
      cites.forEach((c, i) => c.classList.toggle('rec-hl', (i === 0 && t >= 1.1 && t < 2.4) || (i === 2 && t >= 2.6 && t < 3.9)));
      arts.forEach((a, i) => a.classList.toggle('rec-hl', (i === 0 && t >= 1.1 && t < 2.4) || (i === 2 && t >= 2.6 && t < 3.9)));
      cursor(5, t, [[.4, 760, 700], [1.1, ...c1], [2.5, ...c3], [4.3, ...acceptC], [4.5, ...acceptC, 1], [8, acceptC[0] - 30, acceptC[1] + 90]]);
      press(accept, t, 4.5);
      const done = t >= 4.7;
      text(accept, done ? '已验收' : '验收通过'); accept.style.background = done ? '#2f7a55' : '';
      text(vsel, done ? '已验收 v1' : '草稿 v1');
      if (ver) text(q(ver, 'span') || ver, done ? '已验收 v1' : '草稿 v1');
      toast(5, t, 4.9, 8.4, '已验收 v1 · 之后的修改会生成 v2，不覆盖已验收版本'); }
    if (!toastShown) { toastEl.style.opacity = 0; toastEl.style.visibility = 'hidden'; }
  };
  return { ok: true, rows: s1rows.length, nodes: nodes.length, items: itemEls.length, scope: its.length, cites: cites.length, activeClass };
}

(async () => {
  const output = path.resolve(__dirname, '../../assets/video');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  await page.goto('file://' + path.resolve(design));
  await page.evaluate(() => document.fonts.ready);
  console.error(JSON.stringify(await page.evaluate(setup, { scenes: SCENES, starts: STARTS })));
  if (process.env.PREVIEW) {
    for (const t of process.env.PREVIEW.split(',').map(Number)) { await page.evaluate(t => window.render(t), t); await page.screenshot({ path: path.join(process.env.PREVIEW_DIR || '.', `p-${t}.png`) }); }
    await browser.close(); return;
  }
  const mp4 = path.join(output, 'sage-team-collaboration.mp4');
  const encoder = spawn(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', process.env.CRF || '25', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', mp4], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((resolve, reject) => encoder.on('close', code => code ? reject(Error('ffmpeg ' + code)) : resolve()));
  for (let frame = 0; frame < Math.round(FPS * DURATION); frame++) {
    const t = frame / FPS;
    await page.evaluate(t => window.render(t), t);
    const png = await page.screenshot({ type: 'png' });
    if (!encoder.stdin.write(png)) await new Promise(resolve => encoder.stdin.once('drain', resolve));
  }
  encoder.stdin.end(); await done;
  await page.evaluate(t => window.render(t), POSTER);
  await page.screenshot({ path: path.join(output, 'sage-team-collaboration-poster.jpg'), type: 'jpeg', quality: 80 });
  await browser.close();
  console.log(JSON.stringify({ chapters: STARTS, duration: DURATION, size: fs.statSync(mp4).size }));
})().catch(error => { console.error(error); process.exitCode = 1; });
