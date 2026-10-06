/* Static build/catalog contract audit. Browser evidence is recorded separately. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
assert.ok(read('src/visual-draft.css').includes('.v1 .footer.wrap{width:100%;max-width:none;'),'Homepage footer must not leave dark gutters');
assert.ok(read('src/pages/site-shell.js').includes("window.addEventListener('sage:session'"));
assert.ok(read('src/pages/site-shell.js').includes("consult.showModal();"));
assert.ok(read('src/pages/site-shell.js').includes("text('咨询和演示','Consultation and demo')"));
assert.ok(read('src/pages/site-shell.js').includes("text('提交预约','Submit request')"));
assert.ok(read('src/index.template.html').includes('<h3>咨询和演示</h3>'));
assert.ok(read('src/index.template.html').includes('type="submit">提交预约</button>'));
assert.ok(!read('src/pages/profile.html').includes('src="experience.js"'),'Personal settings must not redirect to billing');
assert.ok(read('src/pages/profile.html').includes('id="sage-profile-data"'));
assert.ok(read('src/app.js').includes("const accountLinks=$$('.nav-tools>a[data-account-entry],.mobile-nav>a[data-account-entry]');"),'Homepage account entries must be a collection; a single element aborts globe initialization');
assert.ok(!read('src/pages/ai-team.css').includes('repeat(auto-fit,minmax(78px,1fr))'),'Desktop staff groups must preserve the confirmed single-row layout');
assert.ok(read('src/index.template.html').includes('data-account-entry href="pages/auth.html?return=%2Findex.html">登录</a>'));
assert.ok(read('src/pages/content-pages.js').includes("account.textContent = '登录'; account.title = '使用邮箱或手机号验证登录'"));
assert.ok(read('src/pages/billing.js').includes("document.querySelector('.nav-login').textContent = t('登录','Sign in');"));
assert.ok(read('src/pages/content-pages.css').includes('.pricing-page .segmented button[aria-pressed=true]{background:#aec8e2'));
const context = {window:{}};
vm.runInNewContext(read('src/pages/catalog-data.js'), context);
const catalog = context.window.SAGE_CATALOG;
assert.equal(catalog.people.length, 50);
assert.equal(new Set(catalog.people.map(p=>p.id)).size, 50);
assert.equal(catalog.domains.length, 8);
assert.equal(catalog.scenes.length, 9);
const ids = new Set(catalog.people.map(p=>p.id));
let stages = 0;
for (const person of catalog.people) {
  assert.ok(catalog.domains.includes(person.domain));
  const filename = path.resolve(root, 'pages', person.avatar);
  const image = fs.readFileSync(filename);
  assert.equal(image.toString('ascii',0,4),'RIFF', person.id);
  assert.equal(image.toString('ascii',8,12),'WEBP', person.id);
  assert.ok(person.name && person.role && person.output);
}
for (const scene of catalog.scenes) {
  assert.equal(scene.team.length, 5, scene.id);
  assert.ok(ids.has(scene.owner));
  for (const stage of scene.team) {
    stages++;
    assert.ok(stage.label && stage.accept);
    for (const id of [stage.lead,...stage.contributors,...stage.assurance]) assert.ok(ids.has(id), id);
  }
}
const docSource = read('src/pages/docs-page.js');
const slugs = [...docSource.matchAll(/^    \['([a-z0-9-]+)',/gm)].map(m=>m[1]);
assert.equal(slugs.length,40);
assert.equal(new Set(slugs).size,40);
for (const slug of ['gmv-growth','new-product','supply-delivery','market-entry','customer-journey','data-tools','major-events','business-review','shopify']) assert.ok(slugs.includes(slug));
for (const name of ['team','enterprise','docs']) {
  const fragment = read(`src/pages/${name}.html`), built = read(`pages/${name}.html`);
  if(name==='docs'){
    for(const part of fragment.split('{{SAGE_DOCS_STATIC}}'))assert.ok(built.includes(part),`${name}: build must preserve the source around generated static articles`);
    assert.equal((built.match(/id="doc-readable-/g)||[]).length,40,'All document bodies must be readable without JavaScript');
  }else assert.ok(built.includes(fragment),`${name}: build must include current source`);
  assert.ok(built.includes('class="content-site'));
  assert.ok(built.includes(`href="${name}.html" aria-current="page"`));
  assert.ok(!built.includes('认证、支付与真实任务尚未接入'));
  for (const [,asset,version] of built.matchAll(/(?:src|href)="([^"?]+\.(?:css|js))\?v=([a-f0-9]{12})"/g)) {
    assert.equal(version,createHash('sha256').update(fs.readFileSync(path.join(root,'src/pages',asset))).digest('hex').slice(0,12));
  }
  assert.ok(!/(?:src|href)="[^"?]+\.(?:css|js)"/.test(built),'Page assets must be content-versioned');
  const elementIds = [...built.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  assert.equal(elementIds.length,new Set(elementIds).size,`${name}: duplicate IDs`);
  for (const match of built.matchAll(/(?:src|href)="([^"]+)"/g)) {
    const url=match[1];
    if (/^(?:https?:|data:|#|\?)/.test(url)) continue;
    const filename=path.resolve(root,'pages',url.split(/[?#]/)[0]);
    assert.ok(fs.existsSync(filename),`${name}: missing ${url}`);
    const article=url.match(/\?article=([a-z0-9-]+)/)?.[1];
    if(article)assert.ok(slugs.includes(article),`Unknown article: ${article}`);
  }
}
assert.ok(read('src/pages/team.html').includes('<strong>CMO</strong>'));
assert.ok(read('src/pages/team.html').includes('<strong>CHO</strong>'));
assert.ok(!read('src/pages/team.html').includes('CGOv'));
const teamHero=read('src/pages/team.html').split('<section class="at-hero"')[1].split('</section>')[0];
const enterpriseHero=read('src/pages/enterprise.html').match(/<header class="ep-hero"[\s\S]*?<\/header>/)[0];
assert.ok(teamHero.includes('从专业岗位，到有序协作'));
for(const hero of [teamHero,enterpriseHero])assert.ok(!hero.includes('<a '),'Hero actions must stay removed');
assert.ok(teamHero.includes('at-professional-visual'));
assert.ok(!read('src/pages/team.html').includes('at-business-heading'),'Remove the screenshot business-problem section entirely');
assert.ok(!read('src/pages/ai-team.css').includes('at-business-reading'),'No orphan styles for the removed section');
assert.ok(teamHero.includes('icons/team-human-ai-workspace.png'),'Use the user-approved meeting illustration');
assert.ok(read('src/pages/ai-team.css').includes('.at-professional-visual::after'),'Blend the illustration edges into the white page');
for(const home of ['index.html','index-en.html'])assert.ok(read(home).includes('data-theme="dark"'),'Homepage hero keeps its approved dark background');
const homePalette=read('src/visual-draft.css');
assert.ok(homePalette.includes('.v1 .btn.primary{background:#aec8e2;'));
assert.ok(homePalette.includes('.v1 .btn.primary:hover{background:#c2d8ec}'));
assert.ok(!homePalette.includes('body.v1[data-theme=light] .hero{background:#fff}'),'Do not override the approved hero background');
assert.ok(!teamHero.includes('at-orbit'));
assert.ok(!read('src/pages/team.html').includes('经营工作'));
assert.ok(!read('src/pages/ai-team.css').includes('at-orbit-turn'));
assert.ok(read('src/pages/team.html').indexOf('id="at-staff-directory"')>read('src/pages/team.html').indexOf('class="at-management"'));
assert.ok(read('src/pages/ai-team.js').includes("card.append(image,name,role)"));
assert.ok(read('src/pages/ai-team.css').includes('repeat(var(--staff-count),minmax(0,1fr))'));
for(const note of ['工程方法参考','以上为业务判断示例','外部工程资料不代表'])assert.ok(!read('src/pages/team.html').includes(note),note);
for(const note of ['企业实施结构示意，非实时操作界面','以上为实施与交付结构示意'])assert.ok(!read('src/pages/enterprise.html').includes(note),note);
for(const removed of ['at-work-scene','at-domain-grid','data-person='])assert.ok(!read('src/pages/team.html').includes(removed),removed);
for(const content of ['职责决定问题的问法','方法不能只靠临场发挥','复核需要不同的检查职责','不需要团队的，保持简单','如何判断 AI 团队真的帮上了忙'])assert.ok(read('src/pages/team.html').includes(content),content);
assert.ok(!teamHero.includes('assets/people/'),'Hero must show work, not an employee roster');
for(const removed of ['at-toolbar','at-scene-tools','id="at-status"','id="at-search"'])assert.ok(!read('src/pages/team.html').includes(removed),removed);
assert.ok(!read('src/pages/ai-team.js').includes('at-ensemble-left'));
assert.ok(enterpriseHero.includes('ep-business-chain'));
const enterpriseSource=read('src/pages/enterprise.html');
assert.ok(enterpriseSource.includes('href="trial.html">免费体验'));
assert.ok(!enterpriseSource.includes('查看产品'));
assert.ok(!enterpriseSource.includes('讨论企业需求'));
assert.ok(!enterpriseSource.split('<section id="contact"')[1].includes('<button'),'No duplicate closing consultation button');
assert.ok(read('src/pages/trial.html').includes('id="r-menu-toggle"'));
for(const copy of ['打通企业现有系统与多源数据','沉淀企业的核心业务资产','理解 · 推理 · 执行','生成结果并回写到业务系统','业务执行结果','审批意见','驳回','通过','制度规范','决策规则','合规要求','业务指标定义','统计口径','计算逻辑','业务流程','审批规则','角色与权限','相关文档与过程记录','执行结果与数据指标','更新企业业务系统'])assert.ok(enterpriseHero.includes(copy),copy);
assert.ok(enterpriseHero.includes('非操作按钮'));
for(const label of ['业务系统与数据','企业知识','人工审批','验证与交付'])assert.ok(enterpriseHero.includes(label));
assert.ok(!enterpriseHero.includes('hero-collaboration.png'));
assert.ok(!read('src/pages/team.html').includes('at-connections'));
assert.ok(!read('src/pages/ai-team.js').includes('getContext'));
assert.ok(enterpriseHero.indexOf('<h1>')<enterpriseHero.indexOf('ep-hero-intro'));
assert.ok(read('src/pages/content-pages.js').includes("auth.html?return='"));
for (const icon of ['person','file-earmark-text','database','diagram-3','people','search','shield-lock','bar-chart']) assert.ok(fs.existsSync(path.join(root,'pages/icons',icon+'.svg')));
const sharedRoutes=['team','enterprise','docs','pricing','trial','download','checkout','billing','auth','profile'].flatMap(name=>[name,name+'-en']).concat('product');
for(const route of sharedRoutes){
  const html=read('pages/'+route+'.html');
  assert.ok(html.includes('content-pages.css'),route+': shared visual foundation');
  if(route!=='product')assert.equal((html.match(/new MutationObserver\(initialize\)/g)||[]).length,1,route+': one language initializer');
  assert.ok(!html.includes('src="sage-mark.svg"'),route+': shared logo');
}
assert.ok(read('src/pages/billing.js').includes('class="nav-login"'));
assert.ok(read('src/pages/billing.js').includes("hidden = !session?.authenticated"));
assert.ok(read('src/pages/content-pages.js').includes("account.textContent = '登录';"));
for(const name of ['team','enterprise','pricing','docs','trial','auth']){
 for(const [,asset,version] of read(`pages/${name}.html`).matchAll(/(?:src|href)="([\w-]+\.(?:css|js))\?v=([a-f0-9]{12})"/g))assert.equal(version,createHash('sha256').update(fs.readFileSync(path.join(root,'pages',asset))).digest('hex').slice(0,12),`${name}: stale ${asset}`);
 assert.ok(!/(?:src|href)="[\w-]+\.(?:css|js)"/.test(read(`pages/${name}.html`)),`${name}: unversioned asset`);
}
assert.ok(read('src/pages/content-pages.css').includes('grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)'));
assert.ok(read('src/visual-draft.css').includes('grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)'));
assert.ok(read('src/pages/auth.js').includes('首次验证成功后创建账户，已有账户直接登录。'));
assert.ok(!read('src/pages/auth.js').includes('class="back-link auth-back"'));
console.log(JSON.stringify({scope:'static source/build contracts; not browser or production',pages:7,sharedRoutes:sharedRoutes.length,portraits:50,domains:8,scenes:9,stages,articles:slugs.length,result:'passed'},null,2));
