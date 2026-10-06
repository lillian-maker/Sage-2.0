/* Actual loopback HTTP, never mocked routes or externally delivered OTPs. */
const assert=require('node:assert/strict');
const readline=require('node:readline');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const base=new URL(process.env.SAGE_URL);
assert.equal(base.hostname,'127.0.0.1');
const input=readline.createInterface({input:process.stdin,terminal:false});
async function signIn(page){
 page.setDefaultTimeout(10000);
 await page.goto(new URL('/pages/auth.html?return=%2Fpages%2Fprofile.html',base).href);
 await page.locator('#auth-contact').fill('website-browser@example.test');
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/auth/challenges');
 await page.locator('#request-code').click();assert.equal((await response).status(),201);
 const code=new Promise(resolve=>input.once('line',resolve));process.stdout.write('code requested\n');
 await page.locator('#auth-code').fill(await code);await page.locator('#auth-verify').click();
 await page.waitForURL('**/profile.html');await page.getByText('邮箱（只读）').waitFor();
}
(async()=>{
 const browser=await chromium.launch({channel:'chrome'});
 const first=await browser.newContext(),second=await browser.newContext(),guest=await browser.newContext();
 try{
  const page=await first.newPage(),other=await second.newPage();
  await signIn(page);await signIn(other);
  await page.reload();await page.getByText('邮箱（只读）').waitFor();
  await page.locator('input[autocomplete=nickname]').fill('Browser fixture');
  await page.getByRole('button',{name:'保存名称',exact:true}).click();await page.getByText('名称已保存。').waitFor();
  await page.locator('input[type=checkbox]').check();await page.getByText('通知偏好已保存。').waitFor();
  await page.getByRole('button',{name:'注销其他会话',exact:true}).click();
  await page.getByText('其他会话已注销，当前会话保留。').waitFor();
  assert.equal((await second.request.get(new URL('/api/account/profile',base).href)).status(),401);
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64');
  await page.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:image});
  await page.getByRole('button',{name:'确认上传',exact:true}).click();
  await page.getByRole('img',{name:'当前头像',exact:true}).waitFor();
  await page.getByRole('button',{name:'移除头像',exact:true}).click();await page.getByText('邮箱（只读）').waitFor();
  await page.reload();await page.getByText('邮箱（只读）').waitFor();
  assert.equal(await page.locator('input[autocomplete=nickname]').inputValue(),'Browser fixture');
  assert.equal(await page.locator('input[type=checkbox]').isChecked(),true);
  await page.getByRole('link',{name:'用量明细',exact:true}).click();await page.getByText('示例数据 · 不产生实际扣费').waitFor();
  const anonymous=await guest.newPage();await anonymous.goto(new URL('/',base).href);
  const form=anonymous.locator('#r-contact-form');await form.locator('[name=company]').fill('Browser brand fixture');
  await form.locator('[name=phone]').fill('+8613800000000');await form.locator('[name=goal]').fill('Browser question fixture');
  await form.locator('[type=submit]').click();await form.getByText('预约申请已提交。').waitFor();
 }finally{await browser.close();input.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
