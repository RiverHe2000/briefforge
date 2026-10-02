import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const base=process.env.BRIEFFORGE_URL??'http://127.0.0.1:8788';
const output=fileURLToPath(new URL('../../artifacts/frontend/',import.meta.url));await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:900}});
const results=[],errors=[],runRequests=[];page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(request.method()==='POST'&&/\/runs(?:\?|$)/.test(request.url()))runRequests.push(request.url())});
const step=async(name,fn)=>{await fn();results.push({name,status:'passed'});console.log(`PASS ${name}`)};
try{
 const schema=await (await page.request.get(`${base}/openapi.json`)).json();assert(schema.components.schemas.ProjectCreate.properties.web_enabled,'Restart API to load the new brief schema before this integration test.');
 await page.goto(base,{waitUntil:'domcontentloaded'});await page.getByRole('button',{name:'创建研究项目'}).waitFor();
 let projectId='';
 await step('create public uploaded-only brief and persist custom timeframe',async()=>{
  await page.getByRole('button',{name:'创建研究项目'}).click();
  assert.equal(await page.getByRole('textbox',{name:'研究时间范围'}).inputValue(),'最近12个月');
  const web=page.getByRole('checkbox',{name:'联网搜索公开资料'});assert(await web.isChecked());await web.uncheck();
  await page.getByRole('textbox',{name:'项目名称'}).fill(`资料边界验收 ${Date.now()}`);
  await page.getByRole('textbox',{name:'你想研究什么？'}).fill('基于已上传资料比较 Alpha 和 Beta 的价格与套餐条件。');
  await page.getByRole('textbox',{name:'比较对象'}).fill('Alpha，Beta');
  await page.getByRole('textbox',{name:'研究时间范围'}).fill('2026年1月至9月');
  const responsePromise=page.waitForResponse(response=>response.url()===`${base}/api/projects`&&response.request().method()==='POST');
  await page.getByRole('button',{name:'保存研究简报'}).click();const response=await responsePromise;assert.equal(response.status(),201);
  const created=await response.json();projectId=created.id;assert.equal(created.web_enabled,false);assert.equal(created.time_range,'2026年1月至9月');
  await page.getByRole('dialog').waitFor({state:'hidden'});await page.getByText('仅分析已导入资料 · 使用真实模型').waitFor();
 });
 await step('edit existing brief keeps explicit setting and persists changes',async()=>{
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  assert.equal(await page.getByRole('textbox',{name:'研究时间范围'}).inputValue(),'2026年1月至9月');
  assert.equal(await page.getByRole('checkbox',{name:'联网搜索公开资料'}).isChecked(),false);
  await page.getByRole('textbox',{name:'研究时间范围'}).fill('2026全年');await page.getByRole('checkbox',{name:'联网搜索公开资料'}).check();
  await page.screenshot({path:path.join(output,'12-research-scope-options.png')});
  await page.getByRole('button',{name:'保存研究简报'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
  const saved=await (await page.request.get(`${base}/api/projects/${projectId}`)).json();assert.equal(saved.time_range,'2026全年');assert.equal(saved.web_enabled,true);
  await page.getByText('已导入资料 + 联网搜索公开资料').waitFor();
 });
 await step('synthetic workspace hides web checkbox and submits false',async()=>{
  await page.getByRole('button',{name:'创建研究项目'}).click();await page.getByRole('button',{name:'虚构测试工作区',exact:false}).click();
  assert.equal(await page.getByRole('checkbox',{name:'联网搜索公开资料'}).count(),0);
  await page.getByRole('textbox',{name:'项目名称'}).fill(`虚构资料边界验收 ${Date.now()}`);
  await page.getByRole('textbox',{name:'你想研究什么？'}).fill('核对虚构竞品的功能与套餐范围。');await page.getByRole('textbox',{name:'比较对象'}).fill('虚构 Alpha');
  const responsePromise=page.waitForResponse(response=>response.url()===`${base}/api/projects`&&response.request().method()==='POST');
  await page.getByRole('button',{name:'保存研究简报'}).click();const response=await responsePromise;assert.equal(response.status(),201);
  const created=await response.json();assert.equal(created.mode,'synthetic');assert.equal(created.web_enabled,false);assert.equal(created.time_range,'最近12个月');
  await page.getByRole('dialog').waitFor({state:'hidden'});
 });
 assert.deepEqual(errors,[]);assert.deepEqual(runRequests,[]);results.push({name:'no browser errors and no research/model requests',status:'passed'});
}catch(error){console.error(error);process.exitCode=1;results.push({name:'failure',status:'failed',error:String(error)});await page.screenshot({path:path.join(output,'brief-options-failure.png'),fullPage:true})}finally{await writeFile(path.join(output,'brief-options-results.json'),JSON.stringify(results,null,2));await browser.close()}
