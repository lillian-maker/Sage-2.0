// Generate copyable HTML partials and the public inventory from the catalog source.
const fs=require('node:fs'),path=require('node:path');
const base=__dirname,html=fs.readFileSync(path.join(base,'index.html'),'utf8');
const catalog=JSON.parse(html.split('const catalog=')[1].split(';\nconst categories=')[0]);
const output=path.join(base,'snippets');fs.mkdirSync(output,{recursive:true});
for(const item of catalog) fs.writeFileSync(path.join(output,item.id+'.html'),'<!-- Generated from index.html. HTML partial; asset URLs are relative to UI kit root. -->\n'+item.sample+'\n');
const metadata={name:'Sage UI Kit',edition:'0.1',date:'2026-10-22',scope:'当前 Sage App 设计规范覆盖的 38 类组件；不宣称覆盖未来所有模块；业务接口由实际 App 接入。',themes:['light','dark'],iconCount:32,count:catalog.length,components:catalog.map(({sample,wide,...item})=>item)};
fs.writeFileSync(path.join(base,'components.json'),JSON.stringify(metadata,null,2)+'\n');console.log('Generated '+catalog.length+' HTML snippets and component inventory.');
