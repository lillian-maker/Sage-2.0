// Actual browser rendering against generated local files; identity API is fixture-only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),root=path.resolve(__dirname,'..');
const context={window:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'src/pages/docs-articles-en.js'),'utf8'),context);
const zhLiteral=fs.readFileSync(path.join(root,'src/pages/docs-page.js'),'utf8').split('window.SAGE_DOCS_EN : ')[1].split('\n  ];')[0]+'\n  ]',zhArticles=vm.runInNewContext(zhLiteral);
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.setDefaultTimeout(5000);page.on('pageerror',e=>errors.push(e.message));
 try{
 const fulfill=route=>{const url=new URL(route.request().url());if(url.pathname.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:JSON.stringify({authenticated:false,csrf_token:'fixture',accounts:[]})});const file=path.resolve(root,'.'+url.pathname);if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile())return route.fulfill({status:404,body:''});const type={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';return route.fulfill({contentType:type,body:fs.readFileSync(file)});};
 await page.route('**/*',fulfill);
 for(const article of context.window.SAGE_DOCS_EN){await page.goto('http://127.0.0.1/pages/docs-en.html?article='+article[0]);await page.locator('#sd-article h1').waitFor();assert.equal(await page.locator('#sd-article h1').textContent(),article[2]);assert.ok(!/[\u4e00-\u9fff]/.test(await page.locator('#sd-article').textContent()),article[0]);assert.equal(await page.locator('#sd-article>section:not(.sd-related)').count(),article[4].length);}
 await page.goto('http://127.0.0.1/pages/docs-en.html?article=does-not-exist');await page.getByRole('heading',{name:'Document not found',exact:true}).waitFor();assert.equal(await page.locator('#sd-article h1').textContent(),'Document not found');
 await page.goto('http://127.0.0.1/pages/docs-en.html?article=refunds');await page.locator('#sd-search').fill('authorization');await page.locator('#sd-search-status').filter({hasText:'matching documents'}).waitFor();assert.ok(await page.locator('#sd-chapters a').count()>0);
 for(const width of [1440,768,320]){
  await page.setViewportSize({width,height:1000});
  for(const name of ['team','enterprise','docs']){
   await page.goto('http://127.0.0.1/pages/'+name+'-en.html');await page.locator('main h1').waitFor();if(name==='team')assert.equal(await page.locator('.at-staff-card').count(),50);
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert.equal(overflow,false,`${name} at ${width}px`);
   assert.ok(!/[\u4e00-\u9fff]/.test(await page.locator('main').textContent()),name);
   if(name==='enterprise'){for(let i=0;i<6;i++){await page.locator('[data-phase="'+i+'"]').click();assert.ok(!/[\u4e00-\u9fff]/.test(await page.locator('#ep-phase-panel').textContent()));}}
  }
 }
 const plain=await browser.newPage({javaScriptEnabled:false});await plain.route('**/*',fulfill);
 for(const language of ['', '-en']){await plain.goto('http://127.0.0.1/pages/docs'+language+'.html');assert.equal(await plain.locator('#sd-article noscript details').count(),40);await plain.locator('#doc-readable-refunds summary').click();assert.ok(await plain.locator('#doc-readable-refunds').isVisible());const text=await plain.locator('#doc-readable-refunds').textContent(),expected=(language?context.window.SAGE_DOCS_EN:zhArticles).find(article=>article[0]==='refunds');for(const section of expected[4])for(const paragraph of section)assert.ok(text.includes(paragraph),'Complete refund paragraph must remain readable without JavaScript');if(language)assert.ok(!/[\u4e00-\u9fff]/.test(await plain.locator('#sd-article').textContent()));}await plain.close();
 assert.deepEqual(errors,[]);console.log('Bilingual browser rendering passed: all 40 articles, invalid address, search, 50 employees, 6 phases and 3 viewport widths');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
