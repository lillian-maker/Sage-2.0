// Render the AI team page demo (multi-specialist orchestration and human review) from Sage App design frames.
// Usage: SAGE_APP_FRAMES=<design export dir> FFMPEG=<ffmpeg with libx264> PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/render-team.cjs
// Writes assets/video/sage-team-collaboration.mp4 and sage-team-collaboration-poster.jpg.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const frames = process.env.SAGE_APP_FRAMES;
const ffmpeg = process.env.FFMPEG || 'ffmpeg';
if (!frames) throw new Error('Set SAGE_APP_FRAMES to the Sage App design export directory');
const FPS = 30, WIDTH = 1280, HEIGHT = 800, SCENE = 5.5;
// Each scene: design frame, chapter caption, and a camera move [zoom, focusX, focusY] from → to (1568×980 space).
const scenes = [
  ['33-计划确认卡.png', '计划确认', '主责专家先给出执行计划：步骤、负责专家、预计消耗与所需授权', [1, 784, 490], [1.18, 930, 560]],
  ['32-编排视图.png', '多专家编排', '执行图按阶段分泳道，节点显示专家、技能与状态', [1.05, 700, 420], [1.15, 900, 360]],
  ['57-并行工作卡.png', '并行协作', '没有依赖的步骤由多位专家同时推进', [1, 784, 490], [1.15, 860, 470]],
  ['09-协作.png', '真人审核', '需要人判断的事项进入“待我处理”，由团队成员审批', [1, 784, 490], [1.15, 980, 450]],
  ['48-授权作用域.png', '授权把关', '涉及外部系统的动作，先确认对象、范围与时效', [1, 784, 490], [1.18, 784, 500]],
  ['28-成果预览.png', '验收成果', '结论附带出处，验收后锁定版本', [1.1, 760, 320], [1.1, 760, 560]],
];
const CHAPTERS = scenes.map((_, i) => +(i * SCENE).toFixed(1));
const DURATION = scenes.length * SCENE;
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'sage-team-demo-'));
scenes.forEach(([file], i) => fs.copyFileSync(path.join(frames, file), path.join(work, `s${i}.png`)));
fs.writeFileSync(path.join(work, 'index.html'), `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1568px;height:980px;overflow:hidden;background:#f6f8fc;font-family:"PingFang SC","Microsoft YaHei",Arial,sans-serif}
.scene{position:absolute;inset:0;opacity:0;transform-origin:0 0}.scene img{width:1568px;height:980px;display:block}
#cap{position:absolute;left:40px;bottom:36px;display:flex;align-items:center;gap:16px;padding:16px 24px;border-radius:14px;background:#14243aeb;color:#fff;box-shadow:0 16px 40px #14243a55}
#cap b{font-size:15px;padding:4px 12px;border-radius:999px;background:#396397}#cap span{font-size:22px}
#bar{position:absolute;left:0;top:0;height:5px;background:#396397}
</style></head><body>${scenes.map((_, i) => `<div class="scene" id="s${i}"><img src="s${i}.png"></div>`).join('')}<div id="cap"><b></b><span></span></div><div id="bar"></div>
<script>
const S=${JSON.stringify(scenes.map(s => [s[1], s[2], s[3], s[4]]))},L=${SCENE},T=${DURATION};
const ease=v=>{v=Math.max(0,Math.min(1,v));return v<.5?4*v*v*v:1-Math.pow(-2*v+2,3)/2},mix=(a,b,k)=>a+(b-a)*k;
window.render=t=>{S.forEach(([,,a,b],i)=>{const s=i*L,el=document.getElementById('s'+i),k=ease((t-s)/L);
 const o=t<s-.5||t>s+L+.5?0:Math.min(1,(t-s+.5)/.5,(s+L+.5-t)/.5);el.style.opacity=i===0&&t<.5?1:o;
 const z=mix(a[0],b[0],k),x=mix(a[1],b[1],k),y=mix(a[2],b[2],k);
 const tx=Math.min(0,Math.max(1568-1568*z,784-x*z)),ty=Math.min(0,Math.max(980-980*z,490-y*z));el.style.transform='translate('+tx+'px,'+ty+'px) scale('+z+')';});
 const i=Math.min(S.length-1,Math.floor(t/L));document.querySelector('#cap b').textContent=(i+1)+' / '+S.length+' · '+S[i][0];document.querySelector('#cap span').textContent=S[i][1];
 document.getElementById('bar').style.width=(t/T*100)+'%';};
</script></body></html>`);
(async () => {
  const output = path.resolve(__dirname, '../../assets/video');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1568, height: 980 }, deviceScaleFactor: WIDTH / 1568 });
  await page.goto('file://' + path.join(work, 'index.html'));
  await page.waitForFunction(() => [...document.images].every(image => image.complete && image.naturalWidth));
  const mp4 = path.join(output, 'sage-team-collaboration.mp4');
  const encoder = spawn(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-vf', `scale=${WIDTH}:${HEIGHT}`,
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '29', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', mp4], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((resolve, reject) => encoder.on('close', code => code ? reject(Error('ffmpeg ' + code)) : resolve()));
  for (let frame = 0; frame < FPS * DURATION; frame++) {
    const t = frame / FPS;
    await page.evaluate(t => window.render(t), t);
    const png = await page.screenshot({ type: 'png' });
    if (!encoder.stdin.write(png)) await new Promise(resolve => encoder.stdin.once('drain', resolve));
    if (frame === Math.round(FPS * (SCENE + 2))) await page.screenshot({ path: path.join(output, 'sage-team-collaboration-poster.jpg'), type: 'jpeg', quality: 82 });
  }
  encoder.stdin.end(); await done; await browser.close();
  fs.rmSync(work, { recursive: true, force: true });
  console.log(JSON.stringify({ chapters: CHAPTERS, duration: DURATION, size: fs.statSync(mp4).size }));
})().catch(error => { console.error(error); process.exitCode = 1; });
