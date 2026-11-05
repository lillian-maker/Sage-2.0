// Render the homepage "how Sage works" demo from Sage App design frames.
// Usage:
//   SAGE_APP_FRAMES=<dir with 49/33/32/28 design PNGs> FFMPEG=<ffmpeg with libx264> \
//   PLAYWRIGHT_MODULE=<playwright> node tools/app-demo-video/render.cjs
// Writes assets/video/sage-app-demo.mp4 (H.264, plays everywhere) and sage-app-demo-poster.jpg; re-run after design changes.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const frames = process.env.SAGE_APP_FRAMES;
const ffmpeg = process.env.FFMPEG || 'ffmpeg';
if (!frames) throw new Error('Set SAGE_APP_FRAMES to the Sage App design export directory');
const sources = { ask: '49-首页问候.png', plan: '33-计划确认卡.png', orchestrate: '32-编排视图.png', deliver: '28-成果预览.png' };
const output = path.resolve(__dirname, '../../assets/video');
const FPS = 30, DURATION = 26, WIDTH = 1280, HEIGHT = 800;
// Chapter boundaries shared with the homepage step list (seconds).
const CHAPTERS = [0, 6.7, 19.2];

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'sage-demo-'));
for (const [key, file] of Object.entries(sources)) fs.copyFileSync(path.join(frames, file), path.join(work, key + '.png'));
// All coordinates use the 1568×980 design space of the exported frames.
fs.writeFileSync(path.join(work, 'index.html'), `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1568px;height:980px;overflow:hidden;background:#f6f8fc}
#camera{position:absolute;inset:0;transform-origin:0 0}
.scene{position:absolute;inset:0;opacity:0}.scene img{width:1568px;height:980px;display:block}
.mask{position:absolute;background:#fff}
#caret{position:absolute;width:2px;height:22px;background:#396397}
#bar{position:absolute;left:562px;width:724px;height:38px;border-radius:8px;background:#3963971a;box-shadow:inset 3px 0 0 #396397}
.ring{position:absolute;border:3px solid #c58a2b;border-radius:14px;box-sizing:border-box}
.pulse{position:absolute;width:30px;height:30px;margin:-15px 0 0 -15px;border-radius:50%;background:#39639733}
#cursor{position:absolute;width:28px;height:28px;margin:-3px 0 0 -5px;filter:drop-shadow(0 2px 3px #0005)}
#ripple{position:absolute;border-radius:50%;border:3px solid #396397;box-sizing:border-box}
</style></head><body><div id="camera">
<div class="scene" id="ask"><img src="ask.png"><div class="mask" id="m1" style="top:354px;height:32px"></div><div class="mask" id="m2" style="top:382px;height:28px"></div><div id="caret"></div></div>
<div class="scene" id="plan"><img src="plan.png"><div id="bar"></div></div>
<div class="scene" id="orchestrate"><img src="orchestrate.png"><div class="ring" id="ring" style="left:616px;top:231px;width:166px;height:116px"></div></div>
<div class="scene" id="deliver"><img src="deliver.png"><div class="pulse" id="p1" style="left:982px;top:266px"></div><div class="pulse" id="p2" style="left:704px;top:294px"></div><div class="pulse" id="p3" style="left:764px;top:578px"></div></div>
</div><svg id="cursor" viewBox="0 0 24 24"><path d="M4 2l15 11-7 1 4 8-3 1-4-8-5 5z" fill="#16283c" stroke="#fff" stroke-width="1.5"/></svg><div id="ripple"></div>
<script>
const $=id=>document.getElementById(id),clamp=v=>Math.max(0,Math.min(1,v)),ease=v=>{v=clamp(v);return v<.5?4*v*v*v:1-Math.pow(-2*v+2,3)/2};
const span=(t,a,b)=>ease((t-a)/(b-a)),mix=(a,b,k)=>a+(b-a)*k;
function camera(t){
  // [time, zoom, focusX, focusY]
  const keys=[[0,1,784,490],[0.6,1,784,490],[1.4,1.22,921,436],[5.9,1.22,921,436],[6.6,1,784,490],[7,1,784,490],[7.8,1.12,923,520],[11.2,1.12,923,560],[12.6,1,784,490],[13.5,1,784,490],[14.3,1.08,880,330],[18.6,1.08,900,330],[19.3,1,784,490],[19.7,1.15,760,300],[23,1.15,760,520],[24,1,784,490],[26,1,784,490]];
  let i=0;while(i<keys.length-2&&t>keys[i+1][0])i++;
  const [t0,z0,x0,y0]=keys[i],[t1,z1,x1,y1]=keys[i+1],k=span(t,t0,t1),z=mix(z0,z1,k),x=mix(x0,x1,k),y=mix(y0,y1,k);
  const tx=Math.min(0,Math.max(1568-1568*z,784-x*z)),ty=Math.min(0,Math.max(980-980*z,490-y*z));
  $('camera').style.transform='translate('+tx+'px,'+ty+'px) scale('+z+')';
  return p=>[p[0]*z+tx,p[1]*z+ty];
}
function scenes(t){
  const fade=(a,b)=>span(t,a,b);
  const o={ask:t<6.7?1:t>25.4?fade(25.4,26):1-fade(6.4,7),plan:t<6.4?0:t<13?fade(6.4,7):1-fade(12.9,13.5),orchestrate:t<12.9?0:t<19?fade(12.9,13.5):1-fade(18.9,19.5),deliver:t<18.9?0:t<25.4?fade(18.9,19.5):1-fade(25.4,26)};
  for(const k in o)$(k).style.opacity=o[k];
}
function typing(t){
  const l1=t>25?0:span(t,1.2,4),l2=t>25?0:span(t,4,4.6),x1=550+(1292-550)*l1,x2=550+(662-550)*l2;
  $('m1').style.left=x1+'px';$('m1').style.width=Math.max(0,1295-x1)+'px';
  $('m2').style.left=x2+'px';$('m2').style.width=Math.max(0,665-x2)+'px';
  const onLine2=l1>=1,blink=Math.floor(t*2.2)%2===0||(t>1.2&&t<4.6);
  $('caret').style.left=(onLine2?Math.min(x2,652):x1)+'px';$('caret').style.top=(onLine2?384:358)+'px';$('caret').style.opacity=blink&&t<25?1:0;
}
function details(t){
  const rows=[362,405,449,523,566,640,684,758],r=Math.min(rows.length-1,Math.max(0,Math.floor((t-7.6)/0.5)));
  $('bar').style.top=(rows[r]-19)+'px';$('bar').style.opacity=t>7.4&&t<12.4?1:0;
  const ring=t>13.5?(Math.sin((t-13.5)*4)+1)/2:0;$('ring').style.opacity=t<17?.35+.65*ring:0;
  [['p1',20],['p2',20.6],['p3',22]].forEach(([id,at])=>{const k=clamp((t-at)/1.2);$(id).style.opacity=k>0&&k<1?1-k:0;$(id).style.transform='scale('+(1+k*2.4)+')';});
}
function pointer(t,project){
  // [enter, arrive, click, from, to]
  const moves=[[4.4,5.6,5.8,[1150,720],[1275,504]],[11.4,12.5,12.7,[1000,720],[1228,813]],[15.6,16.8,17,[760,620],[1199,219]],[23.4,24.6,24.8,[1100,420],[1448,34]]];
  const m=moves.find(m=>t>=m[0]-0.3&&t<=m[2]+0.6);
  if(!m){$('cursor').style.opacity=0;$('ripple').style.opacity=0;return;}
  const k=span(t,m[0],m[1]),p=project([mix(m[3][0],m[4][0],k),mix(m[3][1],m[4][1],k)]);
  $('cursor').style.opacity=clamp((t-m[0]+0.3)/0.3)*(1-clamp((t-m[2]-0.3)/0.3));$('cursor').style.left=p[0]+'px';$('cursor').style.top=p[1]+'px';
  const c=clamp((t-m[2])/0.5),size=16+c*50;
  Object.assign($('ripple').style,{opacity:c>0&&c<1?1-c:0,left:(p[0]-size/2)+'px',top:(p[1]-size/2)+'px',width:size+'px',height:size+'px'});
}
window.render=t=>{const project=camera(t);scenes(t);typing(t);details(t);pointer(t,project);};
</script></body></html>`);

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1568, height: 980 }, deviceScaleFactor: WIDTH / 1568 });
  await page.goto('file://' + path.join(work, 'index.html'));
  await page.waitForFunction(() => [...document.images].every(image => image.complete && image.naturalWidth));
  const mp4 = path.join(output, 'sage-app-demo.mp4');
  const encoder = spawn(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-vf', `scale=${WIDTH}:${HEIGHT}`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '25', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', '-an', mp4], { stdio: ['pipe', 'inherit', 'inherit'] });
  const finished = new Promise((resolve, reject) => encoder.on('close', code => code ? reject(Error('ffmpeg ' + code)) : resolve()));
  for (let frame = 0; frame < FPS * DURATION; frame++) {
    const t = frame / FPS;
    await page.evaluate(t => window.render(t), t);
    const png = await page.screenshot({ type: 'png' });
    if (!encoder.stdin.write(png)) await new Promise(resolve => encoder.stdin.once('drain', resolve));
    if (Math.abs(t - 5) < 1e-6) await page.screenshot({ path: path.join(output, 'sage-app-demo-poster.jpg'), type: 'jpeg', quality: 82 });
  }
  encoder.stdin.end();
  await finished;
  await browser.close();
  fs.rmSync(work, { recursive: true, force: true });
  console.log(JSON.stringify({ chapters: CHAPTERS, duration: DURATION, files: fs.readdirSync(output).map(name => [name, fs.statSync(path.join(output, name)).size]) }));
})().catch(error => { console.error(error); process.exitCode = 1; });
