/** Record the real local UI. All research is explicitly labelled synthetic replay. */
import {createRequire} from 'node:module';
import {mkdir,rename,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('playwright');
const base=process.env.BRIEFFORGE_URL??'http://127.0.0.1:8788';
const output=fileURLToPath(new URL('../artifacts/demo/',import.meta.url));
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1280,height:800},deviceScaleFactor:1,recordVideo:{dir:output,size:{width:1280,height:800}}});
const page=await context.newPage();
const video=page.video();
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const started=Date.now();const scenes=[];
const waitUntil=async(seconds)=>{const end=started+seconds*1000;while(Date.now()<end)await new Promise(resolve=>setTimeout(resolve,Math.min(1000,end-Date.now())))};
const caption=async(text)=>{
 scenes.push({at_seconds:Math.round((Date.now()-started)/1000),text});
 await page.evaluate(text=>{
  let panel=document.getElementById('demo-narration');
  if(!panel){panel=document.createElement('aside');panel.id='demo-narration';panel.setAttribute('aria-label','演示解说');Object.assign(panel.style,{position:'fixed',bottom:'22px',left:'50%',transform:'translateX(-50%)',maxWidth:'860px',width:'calc(100% - 100px)',padding:'15px 22px',borderRadius:'10px',background:'rgba(24,62,45,.97)',border:'1px solid rgba(218,233,198,.24)',boxShadow:'0 8px 35px rgba(25,48,30,.18)',color:'#f4f7ef',zIndex:'1000',pointerEvents:'none',fontFamily:'"Noto Sans SC", sans-serif'});document.body.appendChild(panel)}
  panel.replaceChildren();
  const label=document.createElement('div');label.textContent='演示解说 · 虚构资料 · 固定响应回放';Object.assign(label.style,{fontSize:'10px',letterSpacing:'.8px',color:'#bfd29f',marginBottom:'6px'});
  const body=document.createElement('div');body.textContent=text;Object.assign(body.style,{fontSize:'14px',lineHeight:'1.75'});
  panel.append(label,body);
 },text);
 console.log(`SCENE ${scenes.at(-1).at_seconds}s ${text}`);
};
const nav=async(name)=>{await page.getByRole('navigation',{name:'工作台导航'}).getByRole('button',{name}).click();await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}))};
const closeEvidence=async()=>{const close=page.getByRole('button',{name:'关闭证据详情'});if(await close.isVisible())await close.click()};
const chooseSource=async(kind)=>{await page.locator('.source-card').filter({hasText:'森屿笔记'}).filter({hasText:kind}).first().click();await page.getByRole('dialog',{name:'证据详情'}).waitFor()};
const getVersion=async()=>page.getByRole('combobox',{name:'选择报告版本'}).inputValue();
let success=false;
try{
 await page.goto(base,{waitUntil:'domcontentloaded'});await page.getByRole('button',{name:'体验内置案例'}).waitFor();
 await caption('BriefForge：把研究问题，交给一个可以核查、补查和交付的协作团队。');
 await page.getByRole('button',{name:'体验内置案例'}).click();await page.getByRole('heading',{name:'研究概览',exact:true}).waitFor();
 await page.screenshot({path:path.join(output,'cover.png')});
 await waitUntil(14);

 await nav('资料与证据');assert.equal(await page.locator('.source-card').count(),30);
 await caption('30 份虚构资料构成测试案例。旧资料、新公告和套餐条款可以互相核对。');
 await waitUntil(21);await chooseSource('三月存档说明');
 assert((await page.locator('.source-full-text').textContent()).includes('不支持'));
 await caption('先看旧版资料：三月存档声称所有套餐均不支持 SSO。发布日期必须进入判断。');
 await waitUntil(33);

 await closeEvidence();await chooseSource('安全与登录说明');
 assert((await page.locator('.source-full-text').textContent()).includes('企业版支持'));
 await caption('最新安全说明给出更精确的边界：企业版支持 SSO，团队版不支持。');
 await waitUntil(45);await closeEvidence();await nav('研究概览');

 await page.getByRole('button',{name:'确认研究大纲',exact:true}).click();
 await caption('启动前确认问题、比较维度与费用上限。这里明确选择固定响应回放，不调用付费模型。');
 await page.getByRole('combobox',{name:'运行方式'}).selectOption('replay');
 await waitUntil(58);await page.getByRole('button',{name:'确认并开始研究'}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.getByRole('heading',{name:'报告与导出',exact:true}).waitFor({timeout:120000});
 await nav('协作进度');await page.locator('.task-card').first().waitFor();
 await caption('回放完成后查看实际任务记录：专业分工、独立核查，以及遇到缺口后的补查。');
 await waitUntil(72);
 const followup=page.locator('.task-card').filter({hasText:/补查/}).first();
 if(await followup.count())await followup.scrollIntoViewIfNeeded();
 await caption('补查不是无限循环：每个任务有状态和轮次，研究日志保存到数据库。');
 await waitUntil(85);

 await nav('报告与导出');await page.locator('.report-document').waitFor();
 const firstVersion=await getVersion();
 await caption('研究结论汇总为一份报告；套餐、价格条件与尚未确认的问题一起保留。');
 await page.locator('#comparison').scrollIntoViewIfNeeded();
 await waitUntil(96);await page.locator('.evidence-link').first().click();
 const ssoClaim=page.locator('.claim-card').filter({hasText:'· SSO'}).first();
 await ssoClaim.waitFor();await ssoClaim.locator('.quote-card').first().click();
 await page.locator('.source-full-text mark').waitFor();
 await caption('点击结论，定位到研究时保存的原文。高亮的是准确引用，不是重新生成的摘要。');
 await waitUntil(111);await closeEvidence();

 await nav('资料与证据');await chooseSource('当前价格与付款周期');
 await page.getByRole('button',{name:'更新这份资料'}).click();
 const text=page.getByRole('textbox',{name:'资料原文'});const oldText=await text.inputValue();
 assert(oldText.includes('USD 12/席位/月'));
 await text.fill(oldText.replaceAll('USD 12/席位/月','USD 15/席位/月'));
 await caption('现在模拟一份新价格资料：把年付月均价从 12 改为 15 美元，保存为新版本。');
 await waitUntil(124);await page.getByRole('button',{name:'保存为新版本'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
 await chooseSource('当前价格与付款周期');
 const versions=page.getByRole('combobox',{name:'查看资料历史版本'});await versions.waitFor();assert.equal(await versions.locator('option').count(),2);
 await caption('原始资料没有被覆盖。版本 1 与版本 2 都可查看，旧报告仍绑定原有证据。');
 await waitUntil(134);await closeEvidence();await nav('研究概览');
 await page.getByRole('button',{name:'确认研究大纲',exact:true}).click();await page.getByRole('combobox',{name:'运行方式'}).selectOption('replay');await page.getByRole('button',{name:'确认并开始研究'}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});await page.getByRole('heading',{name:'报告与导出',exact:true}).waitFor({timeout:120000});
 const secondVersion=await getVersion();assert.notEqual(secondVersion,firstVersion);
 await caption('资料变化后再次研究，核查受影响的内容并生成报告版本 2，历史版本继续保留。');
 await page.locator('#comparison').scrollIntoViewIfNeeded();assert((await page.locator('.comparison-table').textContent()).includes('15'));
 await waitUntil(149);
 await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
 await page.getByRole('combobox',{name:'选择报告版本'}).selectOption(firstVersion);await page.locator('#comparison').scrollIntoViewIfNeeded();
 await caption('切回版本 1，仍能看到当时的价格。报告版本让变化可以被审阅和解释。');
 await waitUntil(158);await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
 await page.getByRole('combobox',{name:'选择报告版本'}).selectOption(secondVersion);

 await caption('同一报告版本可导出可编辑 Word 与 PPT。文档共享冻结的事实与引用，不额外调用模型。');
 await page.getByRole('button',{name:'导出 Word'}).click();
 await page.locator('.export-notice').filter({hasText:'DOCX'}).getByRole('link',{name:'下载文件'}).waitFor({timeout:120000});
 await page.getByRole('button',{name:'导出 PPT'}).click();
 await page.locator('.export-notice').filter({hasText:'PPTX'}).getByRole('link',{name:'下载文件'}).waitFor({timeout:120000});
 await waitUntil(173);
 await caption('从分工到核查，从证据到交付。这个演示展示的是实际应用流程，所有研究均为虚构资料回放。');
 await waitUntil(181);assert.deepEqual(errors,[]);success=true;
}catch(error){console.error(error);errors.push(String(error));process.exitCode=1;await page.screenshot({path:path.join(output,'recording-failure.png'),fullPage:true})}
finally{
 const duration=(Date.now()-started)/1000;
 await context.close();const temporary=await video.path();
 const destination=path.join(output,success?'briefforge-demo.webm':'briefforge-demo-incomplete.webm');await rename(temporary,destination);
 await browser.close();
 await writeFile(path.join(output,'recording-results.json'),JSON.stringify({status:success?'passed':'failed',duration_seconds:Math.round(duration),video:path.basename(destination),actual_browser_ui:true,synthetic:true,mode:'replay',provider_calls:0,scenes,errors},null,2));
 console.log(`VIDEO ${destination} duration=${Math.round(duration)}s status=${success?'passed':'failed'}`);
}
