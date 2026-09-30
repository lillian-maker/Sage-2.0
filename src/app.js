(() => {
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const data=window.SAGE_CATALOG;
if(!data){throw new Error('场景资料未加载');return;}


const scenes=data.scenes,people=new Map(data.people.map(p=>[p.id,p]));
const stage=$('#scene-stage'),motion=matchMedia('(prefers-reduced-motion: reduce)');
let current=1,batch=0,hovered=false,visible=false;
const homeRoles={"BVS-01":{"owner":"AGT-023","scope":["AGT-002","AGT-003","AGT-016","AGT-017","AGT-021","AGT-022","AGT-023","AGT-024","AGT-025","AGT-026","AGT-027","AGT-028","AGT-030","AGT-031","AGT-032","AGT-035","AGT-040","AGT-041","AGT-045"],"workflow":[{"label":"建单与口径","lead":["AGT-023"],"contributors":["AGT-003","AGT-045"],"assurance":[]},{"label":"渠道与增长诊断","lead":["AGT-023"],"contributors":["AGT-021","AGT-022","AGT-024","AGT-025","AGT-026","AGT-030","AGT-032","AGT-035"],"assurance":[]},{"label":"供需可行性","lead":["AGT-016"],"contributors":["AGT-017","AGT-032","AGT-041"],"assurance":[]},{"label":"行动包装配","lead":["AGT-023"],"contributors":["AGT-027","AGT-028","AGT-031","AGT-040"],"assurance":[]},{"label":"结果复核","lead":["AGT-003"],"contributors":["AGT-002"],"assurance":["AGT-041"]}],"source":"八条价值流原文"},"BVS-02":{"owner":"AGT-008","scope":["AGT-001","AGT-002","AGT-006","AGT-007","AGT-008","AGT-009","AGT-010","AGT-011","AGT-012","AGT-013","AGT-014","AGT-018","AGT-021","AGT-024","AGT-028","AGT-029","AGT-030","AGT-031","AGT-033","AGT-035","AGT-038","AGT-039","AGT-041","AGT-043","AGT-044","AGT-048"],"workflow":[{"label":"机会证据","lead":["AGT-006"],"contributors":["AGT-007","AGT-038","AGT-039"],"assurance":[]},{"label":"产品定义","lead":["AGT-008"],"contributors":["AGT-009","AGT-010","AGT-013"],"assurance":[]},{"label":"质量与供应验证","lead":["AGT-011"],"contributors":["AGT-012","AGT-014","AGT-018"],"assurance":["AGT-044"]},{"label":"上市实验","lead":["AGT-021"],"contributors":["AGT-024","AGT-028","AGT-029","AGT-030","AGT-031","AGT-033","AGT-035"],"assurance":["AGT-043"]},{"label":"投资组合裁决","lead":["AGT-008"],"contributors":["AGT-001","AGT-002","AGT-041","AGT-048"],"assurance":["AGT-044"]}],"source":"八条价值流原文"},"BVS-03":{"owner":"AGT-016","scope":["AGT-002","AGT-014","AGT-015","AGT-016","AGT-017","AGT-018","AGT-019","AGT-020","AGT-021","AGT-026","AGT-040","AGT-041","AGT-042"],"workflow":[{"label":"需求基线","lead":["AGT-016"],"contributors":["AGT-021","AGT-026","AGT-041"],"assurance":[]},{"label":"供应与资金约束","lead":["AGT-014"],"contributors":["AGT-015","AGT-042"],"assurance":[]},{"label":"计划求解","lead":["AGT-016"],"contributors":["AGT-017","AGT-018"],"assurance":[]},{"label":"履约执行","lead":["AGT-019"],"contributors":["AGT-020"],"assurance":[]},{"label":"经营核对","lead":["AGT-040"],"contributors":["AGT-002"],"assurance":["AGT-042"]}],"source":"八条价值流原文"},"BVS-04":{"owner":"AGT-024","scope":["AGT-002","AGT-007","AGT-009","AGT-019","AGT-021","AGT-022","AGT-023","AGT-024","AGT-025","AGT-027","AGT-028","AGT-029","AGT-030","AGT-031","AGT-033","AGT-036","AGT-042","AGT-043","AGT-044","AGT-050"],"workflow":[{"label":"机会与范围","lead":["AGT-024"],"contributors":["AGT-007","AGT-021","AGT-022","AGT-025","AGT-029"],"assurance":[]},{"label":"产品和内容准备","lead":["AGT-009"],"contributors":["AGT-019","AGT-028","AGT-030","AGT-031"],"assurance":["AGT-044"]},{"label":"渠道发布设计","lead":["AGT-023"],"contributors":["AGT-027","AGT-033","AGT-036"],"assurance":["AGT-043","AGT-050"]},{"label":"上线与核对","lead":["AGT-024"],"contributors":["AGT-002"],"assurance":["AGT-042","AGT-044"]},{"label":"首周期复盘","lead":["AGT-021"],"contributors":["AGT-025","AGT-029"],"assurance":["AGT-050"]}],"source":"八条价值流原文"},"BVS-05":{"owner":"AGT-034","scope":["AGT-002","AGT-006","AGT-020","AGT-023","AGT-028","AGT-029","AGT-034","AGT-035","AGT-036","AGT-037","AGT-038","AGT-039","AGT-040","AGT-044"],"workflow":[{"label":"事件汇聚","lead":["AGT-036"],"contributors":["AGT-037","AGT-038","AGT-039"],"assurance":[]},{"label":"问题诊断","lead":["AGT-034"],"contributors":["AGT-006","AGT-020","AGT-035"],"assurance":[]},{"label":"跨域修复","lead":["AGT-023"],"contributors":["AGT-028","AGT-029","AGT-040"],"assurance":["AGT-044"]},{"label":"触达执行","lead":["AGT-034"],"contributors":["AGT-035","AGT-039"],"assurance":[]},{"label":"结果复核","lead":["AGT-038"],"contributors":["AGT-002","AGT-036"],"assurance":["AGT-044"]}],"source":"八条价值流原文"},"BVS-06":{"owner":"AGT-047","scope":["AGT-002","AGT-003","AGT-021","AGT-023","AGT-040","AGT-044","AGT-045","AGT-046","AGT-047","AGT-048","AGT-049","AGT-050"],"workflow":[{"label":"业务定义","lead":["AGT-047"],"contributors":["AGT-002","AGT-003","AGT-021","AGT-023"],"assurance":[]},{"label":"数据设计","lead":["AGT-045"],"contributors":["AGT-040","AGT-044","AGT-046"],"assurance":[]},{"label":"工具实现","lead":["AGT-046"],"contributors":["AGT-047","AGT-048"],"assurance":[]},{"label":"门禁发布","lead":["AGT-047"],"contributors":["AGT-049"],"assurance":["AGT-050","AGT-044"]},{"label":"价值复盘","lead":["AGT-021"],"contributors":["AGT-003","AGT-045"],"assurance":["AGT-050"]}],"source":"八条价值流原文"},"BVS-07":{"owner":"AGT-018","scope":["AGT-002","AGT-005","AGT-011","AGT-012","AGT-013","AGT-014","AGT-017","AGT-018","AGT-020","AGT-021","AGT-027","AGT-029","AGT-036","AGT-037","AGT-038","AGT-041","AGT-043","AGT-044","AGT-050"],"workflow":[{"label":"信号验证","lead":["AGT-018"],"contributors":["AGT-011","AGT-012","AGT-013","AGT-036"],"assurance":["AGT-050"]},{"label":"最小保护","lead":["AGT-002"],"contributors":["AGT-005","AGT-044","AGT-050"],"assurance":[]},{"label":"事件处置","lead":["AGT-018"],"contributors":["AGT-014","AGT-017","AGT-020","AGT-021","AGT-027","AGT-029","AGT-037"],"assurance":["AGT-043","AGT-044"]},{"label":"根因复核","lead":["AGT-038"],"contributors":["AGT-036","AGT-037","AGT-041"],"assurance":["AGT-005"]},{"label":"恢复裁决","lead":["AGT-018"],"contributors":["AGT-002","AGT-043","AGT-044","AGT-050"],"assurance":["AGT-005"]}],"source":"八条价值流原文"},"BVS-08":{"owner":"AGT-001","scope":["AGT-001","AGT-002","AGT-003","AGT-004","AGT-005","AGT-008","AGT-021","AGT-029","AGT-035","AGT-040","AGT-041","AGT-042","AGT-043","AGT-045","AGT-048","AGT-049","AGT-050"],"workflow":[{"label":"经营事实","lead":["AGT-003"],"contributors":["AGT-021","AGT-029","AGT-035","AGT-040","AGT-045"],"assurance":[]},{"label":"风险与财务复核","lead":["AGT-001"],"contributors":["AGT-041","AGT-042","AGT-043"],"assurance":["AGT-005"]},{"label":"能力诊断","lead":["AGT-004"],"contributors":["AGT-008","AGT-048","AGT-049"],"assurance":[]},{"label":"变更发布","lead":["AGT-048"],"contributors":["AGT-002","AGT-049","AGT-050"],"assurance":["AGT-005"]},{"label":"周期裁决","lead":["AGT-001"],"contributors":["AGT-003","AGT-041","AGT-045"],"assurance":["AGT-005","AGT-050"]}],"source":"八条价值流原文"}};
const sceneNotes=[['先找增长的突破口。','把增长约束，变成行动。'],['从真实需求出发。','让新品想法，有据可依。'],['补多少，何时到？','让补货计划接上交付。'],['先看清市场的门槛。','准备齐了，再迈出去。'],['沿着客户的脚步看。','找到体验中断的地方。'],['先把业务问题说清。','让数据成为趁手的工具。'],['先控制影响范围。','恢复之后，还要复核。'],['一起回看这一轮。','把经验带进下一轮。'],['从这家店的现状开始。','先诊断，再决定怎么改。']];
function workflow(scene){return homeRoles[scene.id]?.workflow||scene.team.map(t=>({...t,lead:[t.lead]}))}
function members(step){return [...new Set([...step.lead,...step.contributors,...step.assurance])]}
for(const id of new Set(scenes.flatMap(s=>workflow(s).flatMap(members)))){const img=new Image();img.src=people.get(id).avatar;}
function paint(){
 const scene=scenes[current],steps=workflow(scene),step=steps[batch],ids=members(step);
 $('#scene-title').textContent=scene.title;$('#relay-title').textContent=step.label;
 const list=$('#relay-people');list.replaceChildren();list.style.setProperty('--member-count',ids.length);list.scrollLeft=0;
 ids.forEach(id=>{const p=people.get(id),card=document.createElement('div'),img=document.createElement('img'),name=document.createElement('strong'),role=document.createElement('span');card.className='relay-person';img.src=p.avatar;img.alt=p.name;img.width=64;img.height=64;name.textContent=p.name+' · '+(step.lead.includes(id)?'牵头':step.assurance.includes(id)?'保障':'协作');role.textContent=p.role;card.append(img,name,role);list.append(card)});
 $('#relay-delivery').textContent=scene.team[batch].accept;
 $('#relay-next').textContent=batch<steps.length-1?'交接给：'+steps[batch+1].label:'汇总交付：'+scene.output;
 $('#output-one').textContent=step.label+'成果';$('#output-two').textContent=scene.output;
 $('#note-left').textContent=sceneNotes[current][0];$('#note-right').textContent=sceneNotes[current][1];
 list.getAnimations().forEach(a=>a.cancel());if(!motion.matches)list.animate([{opacity:.15,transform:'translateX(12px)'},{opacity:1,transform:'translateX(0)'}],{duration:350,easing:'ease-out'});
}
function advance(direction){batch+=direction;if(batch>=workflow(scenes[current]).length){current=(current+1)%scenes.length;batch=0}else if(batch<0){current=(current-1+scenes.length)%scenes.length;batch=workflow(scenes[current]).length-1}paint()}
$('#next').addEventListener('click',()=>{current=(current+1)%scenes.length;batch=0;paint()});
stage.addEventListener('mouseenter',()=>hovered=true);stage.addEventListener('mouseleave',()=>hovered=false);
new IntersectionObserver(entries=>visible=entries[0].isIntersecting,{threshold:.15}).observe(stage);
const timer=setInterval(()=>{if(visible&&!hovered&&!stage.contains(document.activeElement)&&!document.hidden&&!motion.matches)advance(1)},3000);
window.addEventListener('pagehide',event=>{if(!event.persisted)clearInterval(timer)});paint();
const assurance={
 evidence:{title:'每个判断，都能回到依据。',body:'保留来源、对象与分析范围。将支持、反证和待验证假设分开，而不是只交付一个答案。',left:'研究产物',rows:['消费者反馈 · 来源','多维标签 · 分析范围','支持与反证 · 判断依据'],gate:'独立复核',right:'可追溯的结论',detail:'产物版本与接受标准',state:'证据不足时，先补证',icon:'document'},
 permission:{title:'能提出建议，不等于能执行。',body:'明确组织、店铺与操作对象。权限和积分预算在模型之外校验，需要人确认的动作不会被自动略过。',left:'待执行方案',rows:['店铺与商品 · 操作对象','读取或写入 · 权限范围','预计与最高消耗 · 积分预算'],gate:'权限校验',right:'有边界的行动',detail:'按授权范围执行',state:'未获授权，不执行',icon:'lock'},
 receipt:{title:'有回执，才有完成的依据。',body:'对照实际回执判断完成、失败或未知。保留已有成果与错误位置，不把“已发起”当作“已完成”。',left:'任务与产物',rows:['已接受产物 · 版本','执行请求 · 具体对象','实际回执 · 对照核验'],gate:'回执核验',right:'可核验的结果',detail:'完成 / 失败 / 未知',state:'状态未知，先核查',icon:'receipt'}
};
function paintAssurance(key){
 const a=assurance[key];$$('[data-assurance]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.assurance===key)));
 $('#assurance-work').innerHTML='<div class="assurance-copy"><h3>'+a.title+'</h3><p>'+a.body+'</p><span class="assurance-boundary">产品机制示意</span></div><div class="assurance-diagram"><div class="evidence-sheet"><div class="sheet-head"><svg viewBox="0 0 24 28" aria-hidden="true"><path d="M4 2h11l5 5v19H4zM15 2v6h5M8 14h8M8 19h6"/></svg><strong>'+a.left+'</strong></div>'+a.rows.map((r,i)=>'<div class="evidence-row"><span class="evidence-pin"></span>'+r+'</div>').join('')+'</div><div class="assurance-gate"><span class="gate-line"></span><div class="gate-symbol"><svg viewBox="0 0 40 44" aria-hidden="true"><path d="M20 3 5 9v12c0 10 15 19 15 19s15-9 15-19V9z"/><path d="m12 20 6 6 12-13"/></svg></div><strong>'+a.gate+'</strong></div><div class="assurance-result"><strong>'+a.right+'</strong><span>'+a.detail+'</span><p>'+a.state+'</p></div></div>';
}
$$('[data-assurance]').forEach(b=>b.addEventListener('click',()=>paintAssurance(b.dataset.assurance)));paintAssurance('evidence');
$('#r-contact-form').addEventListener('submit',e=>{e.preventDefault();$('#r-form-status').textContent='咨询接收服务尚未接通。内容未发送、未保存，请勿在预览中填写敏感资料。';});
})();

(()=>{const $=s=>document.querySelector(s);const D={chains:window.SAGE_CATALOG.scenes};const v1={reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,globePaused:false,globeTime:3,globeFrame:0,globeOptIn:false};
function readGlobeMaterial(){
 const raw=getComputedStyle(document.body).getPropertyValue('--accent').trim();
 let accent=[.239,.337,.431];
 if(/^#[0-9a-f]{6}$/i.test(raw))accent=[1,3,5].map(i=>parseInt(raw.slice(i,i+2),16)/255);
 else if(/^#[0-9a-f]{3}$/i.test(raw))accent=[1,2,3].map(i=>parseInt(raw[i]+raw[i],16)/255);
 else {const rgb=raw.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i);if(rgb)accent=rgb.slice(1,4).map(x=>Math.min(1,Number(x)/255));}
 const light=document.body.dataset.theme==='light',titanium=true;
 return {light,titanium,accent,key:[light,titanium,...accent].join(':')};
}
function initEarth(){
 const globeRoles=window.SAGE_CATALOG.people;
 const canvas=$('#earth-canvas'),stage=$('#globe-stage'),gl=canvas.getContext('webgl',{alpha:true,antialias:true,premultipliedAlpha:false});
 const textureImage=new Image();textureImage.src='{{WORLD_PILOT_ASSET:earth-texture.png}}';
 const goalLabels=[0,1,2].map((_,j)=>{const b=document.createElement('div');b.className='orbit-goal';b.innerHTML='<span class="signal-point" aria-hidden="true"></span><span class="orbit-text"><b></b><small class="orbit-kind"></small></span>';$('#orbit-goals').append(b);return b;});
 let material=readGlobeMaterial();
 let render=()=>{},ready=false,globeInView=true,last=0,lastDraw=0,lastTextureTime=-1,lastWidth=0;

 if(gl){
  const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;};
  try{
   const p=gl.createProgram();gl.attachShader(p,compile(gl.VERTEX_SHADER,'attribute vec2 a;void main(){gl_Position=vec4(a,0.0,1.0);}'));
   gl.attachShader(p,compile(gl.FRAGMENT_SHADER,`precision mediump float;
uniform vec2 size;
uniform float angle;
uniform float daylight;
uniform float titanium;
uniform vec3 accent;
uniform sampler2D earth;
void main(){
 vec2 p=(gl_FragCoord.xy-size*.5)/(min(size.x,size.y)*.455);
 float r=length(p);
 if(r>1.055){gl_FragColor=vec4(0.0);return;}
 if(r>1.0){
  float glow=pow(max(0.0,1.0-(r-1.0)/.055),3.0);
  gl_FragColor=daylight>.5?vec4(.48,.65,.79,glow*.10):(titanium>.5?vec4(.30,.46,.61,glow*.16):vec4(.12,.44,.69,glow*.28));
  return;
 }
 vec3 n=vec3(p,sqrt(max(0.0,1.0-dot(p,p))));
 vec2 uv=vec2(fract(atan(n.x,n.z)/6.2831853+.5+angle/6.2831853),.5-asin(n.y)/3.1415927);
 vec3 tex=texture2D(earth,uv).rgb;
 float diffuse=max(0.0,dot(n,normalize(vec3(-.5,.4,1.0))));
 vec3 color;
 if(daylight>.5){
  // A cool daylight material: ocean blue, silver-white relief, subtle city accents.
  float terrain=smoothstep(.075,.24,dot(tex,vec3(.2126,.7152,.0722)));
  float city=smoothstep(.02,.25,tex.r-tex.b);
  vec3 surface=mix(vec3(.70,.82,.91),vec3(.94,.965,.98),terrain);
  surface=mix(surface,accent,city*.28);
  color=surface*(.88+.14*diffuse);
  color=mix(color,vec3(.80,.90,.97),pow(1.0-n.z,3.0)*.30);
 }else{
  if(titanium>.5){
   float city=smoothstep(.02,.25,tex.r-tex.b);
   float luminance=dot(tex,vec3(.2126,.7152,.0722));
   tex=mix(tex,vec3(.87,.95,1.0)*luminance*1.12,city);
  }
  float light=.48+.8*diffuse;
  vec3 rim=vec3(.12,.37,.6)*pow(1.0-n.z,3.0)*.48;
  color=tex*light+rim;
 }
 gl_FragColor=vec4(color,1.0-smoothstep(.997,1.0,r));
}`));
   gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(p));gl.useProgram(p);
   const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);const a=gl.getAttribLocation(p,'a');gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,2,gl.FLOAT,false,0,0);
   const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tex);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
   const size=gl.getUniformLocation(p,'size'),angle=gl.getUniformLocation(p,'angle'),daylight=gl.getUniformLocation(p,'daylight'),accent=gl.getUniformLocation(p,'accent'),titanium=gl.getUniformLocation(p,'titanium');
   textureImage.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,tex);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,textureImage);ready=true;stage.dataset.render='webgl';};
   render=t=>{const w=Math.min(1000,Math.round(stage.clientWidth*Math.min(devicePixelRatio,1.25))),h=Math.round(w*stage.clientHeight/stage.clientWidth);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;gl.viewport(0,0,w,h);}gl.useProgram(p);gl.uniform2f(size,w,h);gl.uniform1f(angle,t*.065-.24);gl.uniform1f(daylight,material.light?1:0);gl.uniform1f(titanium,material.titanium?1:0);gl.uniform3fv(accent,material.accent);gl.drawArrays(gl.TRIANGLES,0,6);};
   canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();ready=false;});
  }catch(error){stage.dataset.render='fallback';stage.dataset.renderReason=String(error.message);}
 }
 if(!gl||stage.dataset.render==='fallback'){
  const cpuCanvas=document.createElement('canvas');cpuCanvas.id='earth-canvas';cpuCanvas.setAttribute('role','img');cpuCanvas.setAttribute('aria-label','持续转动的地球，展示全球经营目标');canvas.replaceWith(cpuCanvas);
  const ctx=cpuCanvas.getContext('2d'),res=innerWidth<761?360:480;
  cpuCanvas.width=cpuCanvas.height=res;stage.dataset.render='canvas-sphere';
  if(!gl)stage.dataset.renderReason='WebGL unavailable; spherical Canvas renderer enabled';
  let loaded=false;
  const load=()=>{
   if(loaded)return;loaded=true;
   const src=document.createElement('canvas');src.width=textureImage.naturalWidth;src.height=textureImage.naturalHeight;
   const sctx=src.getContext('2d',{willReadFrequently:true});sctx.drawImage(textureImage,0,0);
   const pixels=sctx.getImageData(0,0,src.width,src.height).data,frame=ctx.createImageData(res,res),pts=[],halo=[];
   for(let y=0;y<res;y++)for(let x=0;x<res;x++){
    const nx=(x+.5-res/2)/(res*.455),ny=(res/2-y-.5)/(res*.455),r2=nx*nx+ny*ny,at=(y*res+x)*4;
    if(r2>1.112)continue;
    if(r2>1){halo.push([at,Math.pow(Math.max(0,1-(Math.sqrt(r2)-1)/.055),3)]);continue;}
    const z=Math.sqrt(1-r2),u=(Math.atan2(nx,z)/(2*Math.PI)+.5)*src.width;
    const v=Math.min(src.height-1,Math.floor((.5-Math.asin(ny)/Math.PI)*src.height)),diffuse=Math.max(0,(-nx*.5+ny*.4+z)/1.1874),edge=Math.pow(1-z,3);
    pts.push([at,u,v*src.width,.48+.8*diffuse,edge*.48,.88+.14*diffuse,edge*.30]);
    frame.data[at+3]=Math.round(255*Math.min(1,(1-Math.sqrt(r2))/.003));
   }
   let paletteKey='',dayPixels=null,nightPixels=pixels;
   const oceanColor=[.70,.82,.91],landColor=[.94,.965,.98],rimColor=[204,229.5,247.35];
   const smooth=(lo,hi,value)=>{const t=Math.min(1,Math.max(0,(value-lo)/(hi-lo)));return t*t*(3-2*t);};
   render=t=>{
    if(paletteKey!==material.key){
     paletteKey=material.key;
     if(material.light){
      dayPixels=new Uint8ClampedArray(pixels.length);
      for(let i=0;i<pixels.length;i+=4){
       const terrain=smooth(.075,.24,(pixels[i]*.2126+pixels[i+1]*.7152+pixels[i+2]*.0722)/255);
       const city=smooth(.02,.25,(pixels[i]-pixels[i+2])/255)*.28;
       for(let c=0;c<3;c++){
        const ocean=oceanColor[c],land=landColor[c],surface=ocean+(land-ocean)*terrain;
        dayPixels[i+c]=(surface+(material.accent[c]-surface)*city)*255;
       }
      }
     }
     if(!material.light&&material.titanium){
      nightPixels=new Uint8ClampedArray(pixels.length);
      const reflection=[.87,.95,1.0];
      for(let i=0;i<pixels.length;i+=4){
       const city=smooth(.02,.25,(pixels[i]-pixels[i+2])/255);
       const luminance=(pixels[i]*.2126+pixels[i+1]*.7152+pixels[i+2]*.0722)*1.12;
       for(let c=0;c<3;c++)nightPixels[i+c]=pixels[i+c]+(luminance*reflection[c]-pixels[i+c])*city;
      }
     }else nightPixels=pixels;
     halo.forEach(([at,glow])=>{
      const rgb=material.light?[122,166,201]:material.titanium?[76.5,117.3,155.55]:[31,112,176];
      frame.data[at]=rgb[0];frame.data[at+1]=rgb[1];frame.data[at+2]=rgb[2];frame.data[at+3]=Math.round(glow*(material.light?25.5:material.titanium?40.8:71));
     });
    }
    const shift=(t*.065-.24)/(2*Math.PI)*src.width;
    for(let j=0;j<pts.length;j++){
     const p=pts[j],sx=((Math.floor(p[1]+shift)%src.width)+src.width)%src.width,from=(p[2]+sx)*4;
     if(material.light){
      for(let c=0;c<3;c++){const base=dayPixels[from+c]*p[5];frame.data[p[0]+c]=base+(rimColor[c]-base)*p[6];}
     }else{
      frame.data[p[0]]=nightPixels[from]*p[3]+31*p[4];frame.data[p[0]+1]=nightPixels[from+1]*p[3]+94*p[4];frame.data[p[0]+2]=nightPixels[from+2]*p[3]+153*p[4];
     }
    }
    ctx.putImageData(frame,0,0);
   };
   ready=true;
  };
  textureImage.addEventListener('load',load,{once:true});if(textureImage.complete&&textureImage.naturalWidth)load();
 }
 const draw=now=>{
 v1.globeFrame=0;
 if(document.hidden||!globeInView){last=0;return;}
 const focused=$('#orbit-goals').contains(document.activeElement);
 const moving=!v1.globePaused&&(!v1.reduced||v1.globeOptIn)&&!focused;
 const delta=last?Math.min((now-last)/1000,.08):0;last=now;
 if(moving)v1.globeTime+=delta;
 if(moving&&now-lastDraw<50){v1.globeFrame=requestAnimationFrame(draw);return;}
 lastDraw=now;
 const t=v1.globeTime;if(ready&&(t!==lastTextureTime||stage.clientWidth!==lastWidth)){render(t);lastTextureTime=t;lastWidth=stage.clientWidth;}if(stage.dataset.rotation!==t.toFixed(2))stage.dataset.rotation=t.toFixed(2);
  goalLabels.forEach((b,j)=>{const age=(t+j*6)%18,round=Math.floor((t+j*6)/18),idx=(round*3+j*7)%globeRoles.length,lon=.78-age*.087,lat=[.49,-.05,-.52][j],w=stage.clientWidth,h=stage.clientHeight,r=Math.min(w,h)*.455,rawX=w*.5+Math.sin(lon)*Math.cos(lat)*r,half=b.offsetWidth/2,edge=innerWidth<761?24:40,left=stage.getBoundingClientRect().left,x=Math.max(edge-left+half,Math.min(innerWidth-edge-left-half,rawX)),y=h*.5-Math.sin(lat)*r;const opacity=v1.reduced?1:Math.min(1,age/1.2,(18-age)/1.2);b.style.transform=`translate(${x.toFixed(1)}px,${y.toFixed(1)}px) translate(-50%,-50%)`;b.style.opacity=String(opacity);b.dataset.lit=String(age>2&&age<10);b.style.setProperty('--signal-intensity',String(.48+.52*Math.max(0,1-Math.abs(age-6)/6)));b.style.visibility=opacity<.05?'hidden':'visible';if(b.dataset.orbitGoal!==String(idx)){b.dataset.orbitGoal=String(idx);b.querySelector('.orbit-kind').textContent=globeRoles[idx].name;b.querySelector('b').textContent=globeRoles[idx].role;b.setAttribute('aria-label',globeRoles[idx].role);}});
 
if(moving)v1.globeFrame=requestAnimationFrame(draw);
};
 const wake=()=>{if(!document.hidden&&globeInView&&!v1.globeFrame){last=0;v1.globeFrame=requestAnimationFrame(draw);}};
 const suspend=()=>{cancelAnimationFrame(v1.globeFrame);v1.globeFrame=0;last=0;};
 v1.globeRefresh=()=>{material=readGlobeMaterial();lastTextureTime=-1;wake();};
 if('IntersectionObserver'in window)new IntersectionObserver(entries=>{globeInView=entries[0].isIntersecting;if(globeInView)wake();else suspend();},{rootMargin:'100px'}).observe(stage);
 document.addEventListener('visibilitychange',()=>{if(document.hidden)suspend();else wake();});
 stage.addEventListener('focusin',()=>{suspend();wake();});
 stage.addEventListener('focusout',()=>queueMicrotask(wake));
 textureImage.addEventListener('load',()=>v1.globeRefresh());
 window.addEventListener('resize',()=>v1.globeRefresh());
 window.addEventListener('pageshow',wake);
 window.addEventListener('pagehide',suspend);
 if(v1.reduced)v1.globePaused=true;
 wake();
}


initEarth();document.querySelector('.menu-toggle').addEventListener('click',e=>{const m=$('#mobile-nav');m.hidden=!m.hidden;e.currentTarget.setAttribute('aria-expanded',String(!m.hidden));});$('#mobile-nav').addEventListener('click',()=>{$('#mobile-nav').hidden=true;document.querySelector('.menu-toggle').setAttribute('aria-expanded','false');});})();
