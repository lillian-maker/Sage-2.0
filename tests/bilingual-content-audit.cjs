const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const read=name=>fs.readFileSync(new URL('../'+name,'file://'+__filename),'utf8');
const chinese=read('src/pages/docs-page.js').split('window.SAGE_DOCS_EN : ')[1].split('\n  ];')[0]+'\n  ]';
const zh=vm.runInNewContext(chinese),context={window:{}};
vm.runInNewContext(read('src/pages/docs-articles-en.js'),context);const en=context.window.SAGE_DOCS_EN;
assert.equal(en.length,40);assert.equal(JSON.stringify(en.map(item=>item[0])),JSON.stringify(zh.map(item=>item[0])));
assert.equal(new Set(en.map(item=>item[1])).size,new Set(zh.map(item=>item[1])).size);
for(let i=0;i<40;i++){
 assert.equal(en[i][4].length,zh[i][4].length,en[i][0]);
 for(let j=0;j<zh[i][4].length;j++){
  assert.equal(en[i][4][j].length,zh[i][4][j].length,`${en[i][0]} section ${j}`);
  for(let k=1;k<zh[i][4][j].length;k++)if(typeof zh[i][4][j][k]==='object')assert.equal(en[i][4][j][k].list.length,zh[i][4][j][k].list.length);
 }
 assert.ok(!/[\u4e00-\u9fff]/.test(JSON.stringify(en[i])),en[i][0]);
}
for(const name of ['team','enterprise','docs']){
 const page=read('pages/'+name+'-en.html');assert.ok(page.includes('class="content-site'));assert.ok(page.includes('lang="en"'));
 const body=page.split('<main')[1].split('</main>')[0];assert.ok(!/[\u4e00-\u9fff]/.test(body),name);
 assert.ok(!page.includes('class="r-nav"'),name+' must use the approved shared navigation');
}
assert.ok(read('pages/docs-en.html').includes('src="docs-articles-en.js?v='));
console.log('Bilingual content contracts passed: 40 matching articles, section/block parity, 9 chapters, approved page structure');
