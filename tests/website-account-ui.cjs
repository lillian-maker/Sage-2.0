// Browser response fixtures only: no real customer data, email or database write.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const source=name=>fs.readFileSync(path.join(__dirname,'../src/pages',name),'utf8');
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),page=await browser.newPage();page.setDefaultTimeout(5000);
 const writes=[],errors=[];page.on('pageerror',error=>errors.push(error.message));
 let profile={display_name:'Fixture user',email:'fixture@example.test',avatar_url:null},prefs={product_updates:false};
 const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64'),consultationKeys=[];
 let devices=[{id:'current',client:'Chrome',location:'未知',ip:'127.0.0.1',created_at:'2026-10-06',is_current:true},{id:'other',client:'Safari',location:'未知',ip:'127.0.0.2',created_at:'2026-10-05',is_current:false}];
 const session={authenticated:true,user:{id:'fixture',display_name:'Fixture user'},csrf_token:'fixture-csrf',accounts:[]};
 try{
 await page.route('**/*',route=>{
  const req=route.request(),url=new URL(req.url()),json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  if(url.pathname.startsWith('/api/')){
   const method=req.method();if(method!=='GET'){writes.push(url.pathname);assert.equal(req.headers()['x-csrftoken'],'fixture-csrf');}
   if(url.pathname==='/api/auth/session')return json(session);
   if(url.pathname==='/api/account/profile'){if(method==='PATCH')profile.display_name=req.postDataJSON().display_name;return json(profile);}
   if(url.pathname==='/api/account/preferences'){if(method==='PATCH')prefs=req.postDataJSON();return json(prefs);}
   if(url.pathname==='/api/account/avatar'){assert.ok(method==='DELETE'||req.headers()['content-type'].startsWith('multipart/form-data;'));profile.avatar_url=method==='DELETE'?null:'/api/account/avatar-image?v=fixture';return json({avatar_url:profile.avatar_url});}
   if(url.pathname==='/api/account/avatar-image')return route.fulfill({contentType:'image/png',body:image});
   if(url.pathname==='/api/account/sessions'){const offset=Number(url.searchParams.get('offset')||0);return json({items:devices.slice(offset,offset+1),next_offset:devices.length>offset+1?offset+1:null});}
   if(url.pathname==='/api/account/sessions/revoke'){devices=devices.filter(item=>item.is_current);return json({revoked_count:1});}
   if(url.pathname==='/api/consultations'){consultationKeys.push(req.headers()['idempotency-key']);assert.ok(req.headers()['idempotency-key']);assert.equal(req.postDataJSON().question,'Fixture question');if(consultationKeys.length===1)return json({error:{code:'UNAVAILABLE'}},503);return json({receipt_id:'fixture-receipt'},201);}
   return json({error:{code:'NOT_FOUND'}},404);
  }
  if(url.pathname.endsWith('profile.html'))return route.fulfill({contentType:'text/html',body:source('profile.html')});
  if(url.pathname.endsWith('consultation.html'))return route.fulfill({contentType:'text/html; charset=utf-8',body:'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><form id="r-contact-form"><input name="company" required><input name="phone" required><input name="email"><textarea name="goal" required></textarea><button type="submit">提交预约</button><p role="status"></p></form><script src="site-shell.js"></script></body></html>'});
  const name=path.basename(url.pathname);if(['profile.js','site-shell.js'].includes(name))return route.fulfill({contentType:'text/javascript',body:source(name)});
  return route.fulfill({status:200,body:''});
 });
 await page.goto('http://127.0.0.1/pages/profile.html');await page.getByText('邮箱（只读）').waitFor();
 await page.locator('input[autocomplete=nickname]').fill('Updated fixture');await page.getByRole('button',{name:'保存名称',exact:true}).click();await page.getByText('名称已保存。').waitFor();assert.equal(profile.display_name,'Updated fixture');
 await page.locator('input[type=checkbox]').check();await page.getByText('通知偏好已保存。').waitFor();assert.equal(prefs.product_updates,true);
 await page.getByRole('button',{name:'加载更多会话',exact:true}).click();await page.getByRole('cell',{name:'Safari',exact:true}).waitFor();assert.equal(await page.locator('.profile-sessions tbody tr').count(),2);
 await page.getByRole('button',{name:'注销其他会话'}).click();await page.getByText('其他会话已注销，当前会话保留。').waitFor();assert.equal(devices.length,1);
 assert.equal(await page.getByRole('button',{name:'删除账号',exact:true}).isDisabled(),true);
 await page.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:image});await page.getByRole('button',{name:'确认上传',exact:true}).click();await page.getByRole('img',{name:'当前头像',exact:true}).waitFor();assert.ok(profile.avatar_url);await page.getByRole('button',{name:'移除头像',exact:true}).click();await page.getByText('邮箱（只读）').waitFor();assert.equal(profile.avatar_url,null);
 const count=writes.length;await page.getByRole('link',{name:'用量明细',exact:true}).click();await page.getByText('示例数据 · 不产生实际扣费').waitFor();assert.equal(writes.length,count,'Example usage is read-only');
 await page.goto('http://127.0.0.1/pages/consultation.html');await page.locator('[name=company]').fill('Fixture');await page.locator('[name=phone]').fill('+8613800000000');await page.locator('[name=goal]').fill('Fixture question');await page.locator('#r-contact-form [type=submit]').click();await page.getByText('未能确认提交结果，请重试；相同内容不会重复创建申请。').waitFor();await page.locator('#r-contact-form [type=submit]').click();await page.getByText('预约申请已提交。').waitFor();assert.equal(consultationKeys[0],consultationKeys[1]);assert.deepEqual(errors,[]);
 console.log('Website account and consultation browser fixtures passed');
 }catch(error){console.error({pageErrors:errors,status:await page.locator('[role=status]').allTextContents(),writes});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
