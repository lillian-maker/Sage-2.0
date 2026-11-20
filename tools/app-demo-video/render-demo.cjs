// Record the website product demos from the live Sage App design HTML (same frames as the Figma file).
// Usage: DEMO=home|team SAGE_APP_DESIGN=<sage-all-screens-final.html> FFMPEG=<ffmpeg> PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/render-demo.cjs
//   home → assets/video/sage-home-demo.mp4: what working with Sage feels like (the product's differentiators).
//   team → assets/video/sage-team-collaboration.mp4: how the specialist team is organised and collaborates.
// PREVIEW=1.5,9 PREVIEW_DIR=/tmp renders still frames instead of a video.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const design = process.env.SAGE_APP_DESIGN;
const ffmpeg = process.env.FFMPEG || 'ffmpeg';
if (!design) throw new Error('Set SAGE_APP_DESIGN to sage-all-screens-final.html from the Sage App design export');
const FPS = 30, W = 1440, H = 900;
// Each video: [scene, seconds] in order; chapters list the scene indexes where a page chapter starts.
const VIDEOS = {
  home: { file: 'sage-home-demo', poster: [0, 8.4],
    scenes: [['kickoff', 9.5], ['running', 6.5], ['collab', 7.5], ['wake', 7.5], ['accept', 8], ['playbook', 6.5], ['autopb', 6.5]],
    chapters: [0, 1, 2, 3, 4, 5] },
  team: { file: 'sage-team-collaboration', poster: [3, 6.4],
    scenes: [['experts', 6.5], ['net', 7], ['teams', 6.5], ['orchestration', 9], ['parallel', 6.5], ['scope', 8.5], ['changes', 6.5]],
    chapters: [0, 1, 2, 3, 4, 5, 6] },
};
// Third-party names in the design whose integrations are not confirmed: shown under generic names.
const GENERIC = { 'Jungle Scout': '销量数据连接器', Keepa: '价格数据连接器' };
const SCREEN = { kickoff: 'screen-kickoff', running: 'screen-running', collab: 'screen-collab', wake: 'screen-wake', accept: 'screen-accept',
  playbook: 'screen-playbook', autopb: 'screen-auto-pb', experts: 'screen-experts', net: 'screen-net', teams: 'screen-teams',
  orchestration: 'screen-orchestration', parallel: 'screen-parallel', scope: 'screen-scope', changes: 'screen-changes' };

// Runs inside the design page: isolates the chosen screens into a 1440×900 stage and exposes render(t).
function setup({ list, screenIds, generic }) {
  const FZ = 1440 / 1184, SB = 256;
  const stage = document.createElement('div');
  stage.id = 'stage'; stage.style.cssText = 'position:fixed;left:0;top:0;width:1440px;height:900px;overflow:hidden;background:#fff;z-index:99999';
  document.body.appendChild(stage);
  document.documentElement.style.overflow = 'hidden';
  const css = document.createElement('style');
  css.textContent = `#stage .screen{position:absolute!important;left:0!important;top:0!important;margin:0!important;border-radius:0;box-shadow:none;transform-origin:0 0;will-change:transform,opacity}
  .rec-cursor{position:absolute;width:22px;height:22px;z-index:50;pointer-events:none;filter:drop-shadow(0 2px 3px #14243a55)}
  .rec-ripple{position:absolute;width:36px;height:36px;margin:-18px 0 0 -18px;border-radius:50%;border:2px solid #396397;z-index:49;pointer-events:none}
  .rec-toast{position:absolute;left:50%;bottom:36px;transform:translateX(-50%);display:flex;gap:16px;align-items:center;padding:11px 18px;border-radius:10px;background:#16283c;color:#fff;font-size:13px;z-index:48;box-shadow:0 12px 32px #16283c40;white-space:nowrap;opacity:0;visibility:hidden}
  .rec-toast u{text-decoration:none;color:#9fc0e6}
  .rec-hl{background:#eaf1f9!important;border-radius:6px;transition:none}
  .rec-ring{box-shadow:0 0 0 2px #396397!important;transition:none}
  #stage :is(.solid,.ghost,.textbtn){white-space:nowrap}
  .rec-pressed{filter:brightness(.88);transform:scale(.97)}`;
  document.head.appendChild(css);
  const screens = list.map(([key]) => { const s = document.getElementById(screenIds[key]); stage.appendChild(s); return s; });
  [...document.body.children].forEach(el => { if (el !== stage && el.tagName !== 'SCRIPT' && el.tagName !== 'STYLE') el.style.display = 'none'; });
  const walker = document.createTreeWalker(stage, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) for (const [name, plain] of Object.entries(generic)) if (n.textContent.includes(name)) n.textContent = n.textContent.replaceAll(name, plain);

  const q = (s, sel) => s.querySelector(sel), qa = (s, sel) => [...s.querySelectorAll(sel)];
  const byText = (s, sel, text) => qa(s, sel).find(el => el.textContent.includes(text));
  const leaf = (s, text) => qa(s, '*').find(e => e.children.length === 0 && e.textContent.trim() === text);
  const center = (s, el) => { const a = s.getBoundingClientRect(), b = el.getBoundingClientRect(); return [b.x - a.x + b.width / 2, b.y - a.y + b.height / 2]; };
  const ease = v => { v = Math.max(0, Math.min(1, v)); return v < .5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2; };
  const mix = (a, b, k) => a + (b - a) * k;
  const fade = (t, at, d = .35) => Math.max(0, Math.min(1, (t - at) / d));
  const show = (el, k, dy = 8) => { if (!el) return; el.style.opacity = k; el.style.transform = k >= 1 ? '' : `translateY(${(1 - k) * dy}px)`; };
  const text = (el, value) => { if (el && el.firstChild && el.dataset.recText !== value) { el.dataset.recText = value; [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).forEach((n, i) => { n.textContent = i ? '' : value; }); } };
  const press = (el, t, at) => el && el.classList.toggle('rec-pressed', t >= at && t < at + .22);
  const markDone = (el, on) => { if (!el) return; el.style.background = on ? '#2f7a55' : ''; el.style.borderColor = on ? '#2f7a55' : ''; el.style.color = on ? '#fff' : ''; };
  const camera = (s, t, keys) => {
    let [, z, x, y] = keys[0];
    for (let j = 1; j < keys.length; j++) { const a = keys[j - 1], b = keys[j]; if (t >= a[0]) { const k = ease((t - a[0]) / Math.max(.01, b[0] - a[0])); z = mix(a[1], b[1], k); x = mix(a[2], b[2], k); y = mix(a[3], b[3], k); } }
    s.style.transform = `translate(${-x * z}px,${-y * z}px) scale(${z})`;
  };
  const focus = (y = 40) => [FZ, SB, y];
  const baseTransform = el => { const v = el && getComputedStyle(el).transform; return v && v !== 'none' ? v + ' ' : ''; };
  const scrimOf = sc => [...sc.children].find(e => { const c = getComputedStyle(e), r = e.getBoundingClientRect(), b = sc.getBoundingClientRect();
    return c.position === 'absolute' && r.width >= b.width - 2 && r.height >= b.height - 2 && /rgba\(.+, 0?\.\d+\)/.test(c.backgroundColor); });
  const orch = document.getElementById('screen-orchestration');
  const pickIcon = c => orch && q(orch, `.nd.${c} .st img`)?.src;
  const icons = { done: pickIcon('done'), run: pickIcon('run'), wait: pickIcon('wait') };
  const queueIcon = orch && qa(orch, '.nd').map(n => q(n, '.st img')?.src).find(src => src && !Object.values(icons).includes(src));

  const toastEl = document.createElement('div'); toastEl.className = 'rec-toast'; toastEl.style.zIndex = 5; stage.appendChild(toastEl);
  let current = 0, toastShown = false;
  const tools = (s, i) => {
    const cur = document.createElement('div'); cur.className = 'rec-cursor';
    cur.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.4-6.8L4 20z" fill="#fff" stroke="#16283c" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const rip = document.createElement('div'); rip.className = 'rec-ripple'; s.append(cur, rip);
    return {
      // Cursor path: [[time, x, y, click?], ...] in scene seconds and screen pixels.
      cursor(t, p) {
        if (!p.length || t < p[0][0] - .3) { cur.style.opacity = 0; rip.style.opacity = 0; return; }
        let x = p[0][1], y = p[0][2];
        for (let j = 1; j < p.length; j++) { const [t0, x0, y0] = p[j - 1], [t1, x1, y1] = p[j]; if (t >= t0) { const k = ease((t - t0) / Math.max(.01, t1 - t0)); x = mix(x0, x1, k); y = mix(y0, y1, k); } }
        cur.style.opacity = Math.min(1, (t - p[0][0] + .3) / .3); cur.style.left = x - 3 + 'px'; cur.style.top = y - 2 + 'px';
        const click = p.filter(v => v[3]).map(v => v[0]).find(c => t >= c && t < c + .5);
        if (click !== undefined) { const k = (t - click) / .5; rip.style.opacity = 1 - k; rip.style.left = x + 'px'; rip.style.top = y + 'px'; rip.style.transform = `scale(${.4 + k})`; } else rip.style.opacity = 0;
      },
      toast(t, at, until, html) { if (i !== current) return; toastShown = true; const k = Math.min(fade(t, at, .3), 1 - fade(t, until, .3)); toastEl.style.opacity = k; toastEl.style.visibility = k > 0 ? 'visible' : 'hidden'; if (toastEl.dataset.html !== html) { toastEl.dataset.html = html; toastEl.innerHTML = html; } },
    };
  };

  const SCENES = {
    kickoff(s, { cursor, toast }) {
      const card = q(s, '.h14hd').parentElement, parts = qa(card, '.h14lab, .h14q, .h14opts, dl, .h14chk, .h14need, .h14fold');
      const opts = qa(s, '.h14opts').map(o => [...o.children]), need = qa(s, '.h14need .ghost'), needC = need.map(el => center(s, el));
      const go = byText(s, '.solid', '开始执行'), pill = q(s, '.pill.warn'), goC = center(s, go), pick = [center(s, opts[0][0]), center(s, opts[1][1])];
      opts.flat().forEach(o => o.classList.remove('on'));
      return t => {
        camera(s, t, [[0, 1, 0, 0], [1.4, 1, 0, 0], [3, ...focus(120)], [9.5, ...focus(150)]]);
        show(card, fade(t, .3), 6); parts.forEach((el, i) => show(el, fade(t, .6 + i * .07, .25), 6));
        opts[0][0].classList.toggle('on', t >= 3.4); opts[1][1].classList.toggle('on', t >= 4.2);
        if (need[0]) { text(need[0], t >= 5.2 ? '已安装' : '安装'); markDone(need[0], t >= 5.2); }
        if (need[1]) { text(need[1], t >= 6 ? '已连接' : '连接'); markDone(need[1], t >= 6); }
        cursor(t, [[2.6, 900, 760], [3.25, ...pick[0]], [3.4, ...pick[0], 1], [4.05, ...pick[1]], [4.2, ...pick[1], 1], [5.05, ...needC[0]], [5.2, ...needC[0], 1], [5.85, ...needC[1]], [6, ...needC[1], 1], [6.95, ...goC], [7.1, ...goC, 1], [9.5, goC[0] - 40, goC[1] + 70]]);
        press(go, t, 7.1);
        const on = t >= 7.3; text(go, on ? '已开工' : '开始执行'); go.style.opacity = on ? .55 : 1; text(pill, on ? '进行中' : '待开工'); pill.classList.toggle('warn', !on);
        toast(t, 7.4, 9.6, '已开工 · 先对齐再执行，不会提前消耗 Credits');
      };
    },
    running(s, { cursor, toast }) {
      const rows = qa(s, '.nm').map(n => n.closest('[class]') && n.parentElement.parentElement);
      const bars = rows.map(r => r && qa(r, '*').find(e => { const c = getComputedStyle(e); return e.children.length === 0 && e.getBoundingClientRect().height <= 6 && e.getBoundingClientRect().width > 20 && c.backgroundColor !== 'rgba(0, 0, 0, 0)'; }));
      const widths = bars.map(b => b && b.style.width);
      const wait = q(s, '.h14st.wait'), stuck = q(s, '.h14st.stuck'), waitC = center(s, wait), stuckC = center(s, stuck);
      const filt = leaf(s, '等你处理 1') || leaf(s, '等你处理'), filtC = filt ? center(s, filt) : [1358, 95];
      return t => {
        camera(s, t, [[0, ...focus(20)], [6.5, ...focus(30)]]);
        rows.forEach((r, i) => show(r, fade(t, .2 + i * .12, .3), 8));
        bars.forEach((b, i) => { if (b && widths[i]) b.style.width = `calc(${widths[i]} * ${ease(fade(t, .4 + i * .12, .9))})`; });
        if (wait) wait.classList.toggle('rec-ring', t >= 2 && t < 3.6);
        if (stuck) stuck.classList.toggle('rec-ring', t >= 3.6 && t < 5);
        cursor(t, [[1.4, 900, 600], [2.1, ...waitC], [3.7, ...stuckC], [5.1, ...filtC], [5.25, ...filtC, 1], [6.5, filtC[0] - 20, filtC[1] + 40]]);
        if (filt) filt.classList.toggle('rec-hl', t >= 5.25);
        toast(t, 5.4, 6.6, '一屏看清每件事的进度、卡点和需要你做什么');
      };
    },
    collab(s, { cursor, toast }) {
      const items = qa(s, '.k').map(k => { let el = k; while (el.parentElement && el.parentElement.querySelectorAll('.k').length === 1) el = el.parentElement; return el; });
      const active = (() => { const a = items[0].className.split(' '), b = items[1].className.split(' '); return a.filter(c => c && !b.includes(c)); })();
      items[0].classList.remove(...active);
      const detail = q(s, '.ctx').parentElement, approve = byText(detail, '.solid', '批准'), acts = approve.parentElement, ch = q(detail, '.ch'), card = ch.parentElement;
      const tab = qa(s, '.flt').find(f => f.textContent.includes('需要我决定')), count = tab && q(tab, 'b'), k0 = q(items[0], '.k');
      const pts = items.map(el => center(s, el)), apC = center(s, approve);
      return t => {
        camera(s, t, [[0, ...focus(30)], [7.5, ...focus(40)]]);
        const hover = t < .8 ? -1 : t < 1.8 ? 1 : t < 2.8 ? 2 : t < 3.6 ? 3 : 0;
        items.forEach((el, i) => el.classList.toggle('rec-hl', i === hover && !(i === 0 && t >= 4.1)));
        items[0].classList.toggle(active[0] || 'on', t >= 4.1); show(detail, fade(t, 4.2, .4), 10);
        cursor(t, [[.4, 700, 700], [1.1, pts[1][0] + 60, pts[1][1]], [2.1, pts[2][0] + 60, pts[2][1]], [3.1, pts[3][0] + 60, pts[3][1]], [3.9, pts[0][0] + 60, pts[0][1]], [4.05, pts[0][0] + 60, pts[0][1], 1], [5.3, ...apC], [5.45, ...apC, 1], [7.5, apC[0] + 30, apC[1] + 70]]);
        press(approve, t, 5.45);
        const ok = t >= 5.65; acts.style.visibility = ok ? 'hidden' : '';
        text(ch, ok ? '已批准 · 仅本次任务 · 只读查询' : '需要你确认'); ch.style.color = ok ? '#2f7a55' : '';
        card.style.borderColor = ok ? '#b9dcc8' : ''; card.style.background = ok ? '#f5fbf7' : '';
        if (k0) { text(k0, ok ? '已批准' : '待你审批'); k0.style.color = ok ? '#2f7a55' : ''; }
        if (count) text(count, ok ? '3' : '4');
        toast(t, 5.8, 7.6, '收件只推送需要你决定的事 · 任务详情同步更新');
      };
    },
    wake(s, { cursor, toast }) {
      const lv = ['只汇报（默认）', '起草建议', '主动发起任务'].map(label => qa(s, 'b').find(b => b.textContent.trim() === label)?.parentElement).filter(Boolean), lvC = lv.map(el => center(s, el));
      const nums = qa(s, 'b').filter(b => /^[\d,]+$/.test(b.textContent.trim())), vals = nums.map(b => +b.textContent.replace(/,/g, ''));
      const tags = qa(s, '.h14tag'), save = byText(s, '.solid', '保存'), saveC = save ? center(s, save) : [1089, 94];
      const lvOn = lv.findIndex(el => el.classList.contains('on'));
      return t => {
        camera(s, t, [[0, ...focus(20)], [7.5, ...focus(30)]]);
        tags.forEach((g, i) => show(g, fade(t, .2 + i * .06, .25), 4));
        nums.forEach((b, i) => text(b, Math.round(vals[i] * ease(fade(t, 1 + i * .35, 1.1))).toLocaleString('en-US')));
        const hov = t >= 3.4 && t < 4.3 ? 1 : t >= 4.3 && t < 5.1 ? 2 : -1;
        lv.forEach((el, i) => el.classList.toggle('rec-hl', i === hov));
        if (lvOn >= 0) lv[lvOn].classList.toggle('rec-ring', t >= 5.3);
        cursor(t, [[2.8, 700, 720], [3.4, ...lvC[1]], [4.3, ...lvC[2]], [5.1, ...lvC[0]], [5.25, ...lvC[0], 1], [6.1, ...saveC], [6.25, ...saveC, 1], [7.5, saveC[0] - 30, saveC[1] + 40]]);
        press(save, t, 6.25);
        toast(t, 6.4, 7.6, '规则先过滤，专家再判断 · 不会在飞书里发言或外发');
      };
    },
    accept(s, { cursor, toast }) {
      const chk = qa(s, '.h14chk'), boxes = chk.map(c => q(c, '.h14box')), ev = chk.map(c => q(c, '.ev'));
      const head = q(q(s, '.h14hd'), 'span'), btn = byText(s, '.solid', '验收通过'), btnC = center(s, btn), pill = q(s, '.pill');
      boxes.forEach(x => x && x.classList.remove('ok'));
      return t => {
        camera(s, t, [[0, ...focus(60)], [8, ...focus(90)]]);
        const ticked = boxes.map((_, i) => t >= .9 + i * .55);
        boxes.forEach((x, i) => x && x.classList.toggle('ok', ticked[i]));
        ev.forEach((e, i) => e && e.classList.toggle('rec-hl', t >= .9 + i * .55 && t < 1.45 + i * .55));
        if (head) text(head, `验收清单 ${ticked.filter(Boolean).length}/${boxes.length} 已满足 · 德国站竞品格局报告 v2`);
        cursor(t, [[3.6, 700, 650], [4.6, ...btnC], [4.8, ...btnC, 1], [8, btnC[0] - 30, btnC[1] + 80]]);
        press(btn, t, 4.8);
        const done = t >= 5; text(btn, done ? '已验收' : '验收通过'); btn.style.background = done ? '#2f7a55' : ''; if (pill) text(pill, done ? '已验收' : '待你验收');
        toast(t, 5.1, 8.1, '已验收 v2 · 每条验收都附证据，退回只返工相关部分');
      };
    },
    playbook(s, { cursor, toast }) {
      const dlg = q(s, '.g11dlg'), dT = baseTransform(dlg), scrim = scrimOf(s);
      const rows = dlg ? qa(dlg, ':scope > *') : [], save = dlg && byText(dlg, '.solid', '保存'), saveC = save ? center(s, save) : [940, 734];
      const vars = dlg ? qa(dlg, '*').filter(e => e.children.length === 0 && /^\{.+\}/.test(e.textContent.trim())) : [];
      return t => {
        camera(s, t, [[0, ...focus(60)], [6.5, ...focus(80)]]);
        const k = Math.min(ease(fade(t, .3, .4)), 1 - fade(t, 4.6, .35));
        if (scrim) scrim.style.opacity = Math.min(fade(t, .2), 1 - fade(t, 4.6, .35));
        if (dlg) { dlg.style.opacity = k; dlg.style.transform = `${dT}scale(${.97 + .03 * k})`; }
        rows.forEach((r, i) => show(r, fade(t, .5 + i * .1, .25), 6));
        vars.forEach((v, i) => v.classList.toggle('rec-hl', t >= 2.2 + i * .45 && t < 2.9 + i * .45));
        cursor(t, [[1.8, 700, 600], [3.9, ...saveC], [4.1, ...saveC, 1], [6.5, saveC[0] - 40, saveC[1] + 40]]);
        if (save) press(save, t, 4.1);
        toast(t, 4.7, 6.6, '已存为 Playbook · 换个市场或品类直接复用');
      };
    },
    autopb(s, { cursor, toast }) {
      const tab = qa(s, 'span').find(e => e.textContent.trim() === 'Playbook'), tabC = tab ? center(s, tab) : [418, 290];
      const card = qa(s, '*').find(e => e.textContent.includes('新市场进入评估') && e.textContent.includes('每次运行前') && e.children.length > 1 && e.getBoundingClientRect().height < 140);
      const save = qa(s, '.solid').find(e => e.textContent.trim() === '保存'), saveC = save ? center(s, save) : [330, 851];
      return t => {
        camera(s, t, [[0, ...focus(30)], [6.5, ...focus(60)]]);
        if (card) card.classList.toggle('rec-hl', t >= 1.2 && t < 3.6);
        cursor(t, [[.3, 700, 500], [.95, ...tabC], [1.1, ...tabC, 1], [4.1, ...saveC], [4.3, ...saveC, 1], [6.5, saveC[0] + 40, saveC[1] - 30]]);
        if (save) press(save, t, 4.3);
        toast(t, 4.5, 6.6, '已保存 · 启用后每月 1 日运行，结果进入收件');
      };
    },
    experts(s, { cursor, toast }) {
      const asst = q(s, '.ex'), cards = qa(s, '.card'), cardsC = cards.map(c => center(s, c));
      cards.forEach(c => c.classList.remove('hover'));
      return t => {
        camera(s, t, [[0, 1, 0, 0], [1.4, 1, 0, 0], [3, ...focus(30)], [6.5, ...focus(70)]]);
        show(asst, fade(t, .2), 6); cards.forEach((c, i) => show(c, fade(t, .5 + i * .12, .3), 10));
        const hov = t >= 2.4 ? Math.min(cards.length - 1, Math.floor((t - 2.4) / .7)) : -1;
        cards.forEach((c, i) => c.classList.toggle('rec-ring', i === hov));
        const p = cardsC.slice(0, 6).map((c, i) => [2.4 + i * .7, c[0], c[1] - 20]);
        cursor(t, [[1.8, 900, 500], ...p, [6.5, p.at(-1)[1] + 10, p.at(-1)[2] + 10]]);
        toast(t, 4.8, 6.6, '53 位数字专家按专业域分组 · 每位都有职责、技能与工具');
      };
    },
    net(s, { cursor, toast }) {
      const edges = q(s, '.g11edges'), rows = qa(s, '.g11row'), rowsC = rows.map(r => center(s, r));
      const graph = edges && edges.parentElement, nodes = graph ? [...graph.children].filter(e => e !== edges) : [];
      return t => {
        camera(s, t, [[0, ...focus(30)], [7, ...focus(50)]]);
        if (edges) { edges.style.opacity = ease(fade(t, .8, .9)); }
        nodes.forEach((n, i) => { const k = ease(fade(t, .2 + i * .1, .35)); n.style.opacity = k; });
        rows.forEach((r, i) => r.classList.toggle('rec-hl', t >= 2.6 + i * .8 && t < 3.4 + i * .8));
        cursor(t, [[2.2, 900, 500], ...rowsC.map((c, i) => [2.6 + i * .8, c[0] - 60, c[1]]), [7, rowsC.at(-1)[0] - 40, rowsC.at(-1)[1] + 50]]);
        toast(t, 5.6, 7.1, '协作网络来自真实交接与复核记录 · 组队越来越准');
      };
    },
    teams(s, { cursor, toast }) {
      const cards = qa(s, '.t').map(x => x.parentElement), at = qa(s, '.ghost').filter(g => g.textContent.includes('@ 团队'));
      const atC = at.map(g => center(s, g)), cardsC = cards.map(c => center(s, c));
      return t => {
        camera(s, t, [[0, ...focus(30)], [6.5, ...focus(40)]]);
        cards.forEach((c, i) => show(c, fade(t, .2 + i * .15, .35), 10));
        cards.forEach((c, i) => c.classList.toggle('rec-ring', (i === 1 && t >= 1.4 && t < 2.4) || (i === 2 && t >= 2.4 && t < 3.3) || (i === 0 && t >= 3.3)));
        cursor(t, [[1, 900, 600], [1.4, cardsC[1][0], cardsC[1][1] - 30], [2.4, cardsC[2][0], cardsC[2][1] - 30], [3.3, cardsC[0][0], cardsC[0][1] - 30], [4.1, ...atC[0]], [4.25, ...atC[0], 1], [6.5, atC[0][0] - 30, atC[0][1] + 40]]);
        press(at[0], t, 4.25);
        toast(t, 4.4, 6.6, '已在新任务中 @ 新市场进入小组 · 5 位专家一次组齐');
      };
    },
    orchestration(s, { cursor }) {
      const nodes = qa(s, '.nd'), st = nodes.map(n => ({ n, icon: q(n, '.st img'), label: q(n, '.m span'), cost: q(n, '.m span:last-child'), base: q(n, '.m span:last-child').textContent }));
      const drawer = q(s, '.conflict').closest('aside') || q(s, '.conflict').parentElement, head = qa(s, 'b');
      const progress = head.find(b => /\/ 9/.test(b.textContent)), credits = head.find(b => b.textContent.trim() === '186'), target = center(s, nodes[4]);
      nodes[4].classList.remove('sel');
      const label = { done: '已完成', run: '进行中', wait: '待你确认', queue: '排队' };
      return t => {
        camera(s, t, [[0, ...focus(30)], [9, ...focus(46)]]);
        const v = []; v[0] = t < .9 ? 'run' : 'done'; [1, 2, 3].forEach(i => { v[i] = t < 1.3 ? 'queue' : 'run'; });
        if (t >= 2.7) v[1] = 'done'; if (t >= 3.5) v[3] = 'done'; v[4] = t < 4.2 ? 'queue' : 'wait'; [5, 6, 7, 8].forEach(i => { v[i] = 'queue'; });
        st.forEach(({ n, icon, label: l, cost, base }, i) => {
          n.classList.remove('done', 'run', 'wait'); if (v[i] !== 'queue') n.classList.add(v[i]);
          if (icon) { icon.src = v[i] === 'queue' ? queueIcon : icons[v[i]]; icon.style.transform = v[i] === 'run' ? `rotate(${t * 300}deg)` : ''; }
          text(l, label[v[i]]); text(cost, v[i] === 'done' || v[i] === 'run' ? base.replace('约 ', '') : (/\d/.test(base) ? (base.startsWith('约') ? base : '约 ' + base) : base));
        });
        nodes[4].classList.toggle('sel', t >= 4.9); nodes[4].style.boxShadow = t >= 4.2 && t < 4.9 ? `0 0 0 ${3 + 3 * Math.sin(t * 9)}px #e8b04a55` : '';
        text(progress, `${v.filter(x => x === 'done').length} / 9 步`); text(credits, String(Math.round(186 * Math.min(1, t / 4.2))));
        const dk = ease(fade(t, 5.05, .45)); drawer.style.opacity = dk; drawer.style.transform = `translateX(${(1 - dk) * 40}px)`;
        cursor(t, [[3.9, 760, 640], [4.75, ...target], [4.9, ...target, 1], [6.2, 1200, 160], [9, 1215, 170]]);
      };
    },
    parallel(s, { cursor }) {
      const rows = qa(s, '.d9row').map(r => ({ r, icon: q(r, 'img.ic'), dn: q(r, '.dn'), tm: q(r, '.tm'), dn0: q(r, '.dn').textContent, tm0: q(r, '.tm').textContent }));
      const secs = v => { const m = v.match(/(\d+) 分 (\d+) 秒/); return m ? +m[1] * 60 + +m[2] : null; };
      const fmt = v => `${Math.floor(v / 60)} 分 ${String(Math.floor(v % 60)).padStart(2, '0')} 秒`;
      const ph = q(s, '.ph'), work = q(s, '.d9work'), rowC = center(s, rows[0].r);
      return t => {
        camera(s, t, [[0, ...focus(60)], [6.5, ...focus(110)]]);
        rows.forEach(({ r, icon, dn, tm, dn0, tm0 }, i) => {
          const done = i === 0 && t >= 3.6, base = secs(tm0);
          if (base !== null) text(tm, fmt(base + (done ? 3.6 : t) * 4));
          if (icon && i < 3) { icon.src = done ? icons.done : icons.run; icon.style.transform = done ? '' : `rotate(${t * 300}deg)`; }
          text(dn, done ? '竞品格局分析 · 已交给望野汇总' : dn0); r.style.opacity = i === 0 && done ? .7 : 1;
        });
        text(ph, t >= 3.6 ? '正在等待 2 位专家 · 1 个在后台' : '正在等待 3 位专家 · 1 个在后台'); text(work, `已工作 ${fmt(17 * 60 + 5 + t * 4)}`);
        cursor(t, [[4.6, 900, 700], [5.6, rowC[0] + 120, rowC[1]], [6.5, rowC[0] + 130, rowC[1] + 4]]);
      };
    },
    scope(s, { cursor, toast }) {
      const split = q(s, '.b9split'), menu = q(s, '.b9scope'), car = q(s, '.car'), its = qa(menu, '.it'), ok = q(split, '.solid');
      const card = q(s, '.confirm'), ch = q(card, '.ch'), acts = q(card, '.acts'), pill = q(s, '.pill.warn');
      const step = qa(s, '.step')[3], small = step && q(step, 'small'), icon = step && q(step, 'img.ic'), sdiv = step && q(step, '.s'), icon0 = icon && icon.src;
      menu.style.transition = 'none';
      const carC = center(s, car), okC = center(s, ok), itC = its.map(it => center(s, it));
      menu.style.display = 'none';
      return t => {
        camera(s, t, [[0, ...focus(120)], [8.5, ...focus(150)]]);
        const open = t >= 1.35 && t < 4.15; menu.style.display = open ? '' : 'none';
        const hov = t < 2.1 ? 0 : t < 2.9 ? 1 : t < 3.6 ? 2 : 0; its.forEach((it, i) => it.classList.toggle('rec-hl', open && i === hov));
        cursor(t, [[.5, 760, 780], [1.2, ...carC], [1.35, ...carC, 1], [2.1, ...itC[1]], [2.9, ...itC[2]], [3.7, ...itC[0]], [3.95, ...itC[0], 1], [4.7, ...okC], [4.9, ...okC, 1], [8.5, okC[0] + 60, okC[1] + 80]]);
        press(ok, t, 4.9);
        const done = t >= 5.1; acts.style.display = done ? 'none' : '';
        text(ch, done ? '已批准 · 仅本次任务 · 只读查询' : '需要你确认'); ch.style.color = done ? '#2f7a55' : '';
        card.style.borderColor = done ? '#b9dcc8' : ''; card.style.background = done ? '#f5fbf7' : '';
        text(pill, done ? '进行中' : '待你确认'); pill.classList.toggle('warn', !done);
        if (small) text(small, done ? '望野 · 正在估算销量' : '等待你确认数据使用');
        if (icon) { icon.src = done ? icons.run : icon0; icon.style.transform = done ? `rotate(${t * 300}deg)` : ''; }
        if (sdiv) sdiv.style.color = done ? 'var(--text-1, #16283c)' : '';
        toast(t, 5.2, 8.6, '已批准 · 仅对这一次查询生效 <u>撤销</u>');
      };
    },
    changes(s, { cursor }) {
      const btn = qa(s, '*').find(e => e.children.length === 0 && e.textContent.trim() === '查看差异');
      const pop = qa(s, '.hd').find(e => e.textContent.includes('恢复到'))?.parentElement, pT = baseTransform(pop), scrim = scrimOf(s);
      const restore = pop && qa(pop, '*').find(e => e.children.length === 0 && e.textContent.includes('恢复到'));
      const btnC = btn ? center(s, btn) : [1390, 224], resC = restore ? center(s, restore) : [940, 393], marks = pop ? qa(pop, 'del, ins, s, mark, .del, .ins') : [];
      return t => {
        camera(s, t, [[0, ...focus(40)], [6.5, ...focus(60)]]);
        if (scrim) scrim.style.opacity = fade(t, 1.3, .35);
        if (pop) { const k = ease(fade(t, 1.4, .35)); pop.style.opacity = k; pop.style.transform = `${pT}translateY(${(1 - k) * 10}px)`; pop.style.visibility = k > 0 ? 'visible' : 'hidden'; }
        marks.forEach((m, i) => m.classList.toggle('rec-hl', t >= 2.4 + i * .6 && t < 3.2 + i * .6));
        cursor(t, [[.3, 1300, 400], [1.05, ...btnC], [1.2, ...btnC, 1], [3.8, ...resC], [6.5, resC[0] + 6, resC[1] + 4]]);
        if (restore) restore.classList.toggle('rec-hl', t >= 4);
      };
    },
  };

  const starts = list.reduce((a, [, d], i) => (a.push(i ? a[i - 1] + list[i - 1][1] : 0), a), []);
  const renders = list.map(([key], i) => { const t0 = performance.now(); const r = SCENES[key](screens[i], tools(screens[i], i)); window.__initLog = (window.__initLog || '') + key + ':' + Math.round(performance.now() - t0) + 'ms '; return r; });
  window.render = T => {
    const idx = Math.max(0, starts.findLastIndex(s => T >= s)); current = idx; toastShown = false;
    screens.forEach((s, i) => { const st = starts[i], en = st + list[i][1]; s.style.opacity = i === 0 && T < .01 ? 1 : (T >= st - .4 && T < en ? Math.min(1, (T - st + .4) / .4) : 0); s.style.zIndex = i === idx ? 2 : 1; });
    renders.forEach((r, i) => r(T - starts[i]));
    if (!toastShown) { toastEl.style.opacity = 0; toastEl.style.visibility = 'hidden'; }
  };
  return { ok: true, starts, init: window.__initLog };
}

(async () => {
  // SCENES=running:3,wake:3 renders an ad-hoc selection, for checking single scenes.
  const video = process.env.SCENES ? { file: 'scene-check', poster: [0, 0], scenes: process.env.SCENES.split(',').map(v => [v.split(':')[0], +v.split(':')[1]]), chapters: [0] } : VIDEOS[process.env.DEMO];
  if (!video) throw new Error('Set DEMO to ' + Object.keys(VIDEOS).join(' or '));
  const starts = video.scenes.reduce((a, [, d], i) => (a.push(i ? a[i - 1] + video.scenes[i - 1][1] : 0), a), []);
  const duration = starts.at(-1) + video.scenes.at(-1)[1];
  const output = path.resolve(__dirname, '../../assets/video');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  await page.goto('file://' + path.resolve(design));
  await page.evaluate(() => document.fonts.ready);
  console.error(JSON.stringify(await page.evaluate(setup, { list: video.scenes, screenIds: SCREEN, generic: GENERIC })));
  if (process.env.PREVIEW) {
    for (const t of process.env.PREVIEW.split(',').map(Number)) { await page.evaluate(t => window.render(t), t); await page.screenshot({ path: path.join(process.env.PREVIEW_DIR || '.', `${process.env.DEMO}-${t}.png`) }); }
    await browser.close(); return;
  }
  const mp4 = path.join(output, video.file + '.mp4');
  const encoder = spawn(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', process.env.CRF || '27', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', mp4], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((resolve, reject) => encoder.on('close', code => code ? reject(Error('ffmpeg ' + code)) : resolve()));
  for (let frame = 0; frame < Math.round(FPS * duration); frame++) {
    await page.evaluate(t => window.render(t), frame / FPS);
    const png = await page.screenshot({ type: 'png' });
    if (!encoder.stdin.write(png)) await new Promise(resolve => encoder.stdin.once('drain', resolve));
  }
  encoder.stdin.end(); await done;
  await page.evaluate(t => window.render(t), starts[video.poster[0]] + video.poster[1]);
  await page.screenshot({ path: path.join(output, video.file + '-poster.jpg'), type: 'jpeg', quality: 80 });
  await browser.close();
  console.log(JSON.stringify({ demo: process.env.DEMO, chapters: video.chapters.map(i => starts[i]), duration, size: fs.statSync(mp4).size }));
})().catch(error => { console.error(error); process.exitCode = 1; });
