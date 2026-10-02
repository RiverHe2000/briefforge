import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const base = process.env.BRIEFFORGE_URL ?? 'http://127.0.0.1:8788';
const label = process.env.BRIEFFORGE_QA_LABEL ?? '';
assert(/^[a-z0-9_-]*$/.test(label), 'QA artifact labels may contain lowercase letters, numbers, _ and - only.');
const output = path.join(fileURLToPath(new URL('../../artifacts/frontend/collaboration/', import.meta.url)), label);
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true});
const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
const get = async resource => {
  const response = await context.request.get(`${base}/api${resource}`);
  assert.equal(response.status(), 200, resource);
  return response.json();
};
const projects = await get('/projects');
const project = process.env.BRIEFFORGE_PROJECT
  ? projects.find(item => item.id === process.env.BRIEFFORGE_PROJECT)
  : projects.find(item => item.latest_report_id);
assert(project, 'A completed demo project is required. This test never creates or runs research.');
const reports = (await get(`/projects/${project.id}/reports`)).sort((a, b) => b.version - a.version);
const runsBefore = await get(`/projects/${project.id}/runs`);
await context.addInitScript(id => localStorage.setItem('briefforge.project', id), project.id);
const page = await context.newPage();
const results = [], errors = [], writes = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) writes.push({method: request.method(), url: request.url()});
});
const step = async (name, fn) => {await fn(); results.push({name, status: 'passed'}); console.log(`PASS ${name}`);};
try {
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await page.locator('.report-document').waitFor();
  await step('report shows its own persisted collaboration record and honest metrics', async () => {
    const data = await get(`/runs/${reports[0].run_id}/collaboration`);
    const panel = page.getByRole('region', {name: '协作带来的变化'});
    await panel.locator('.impact-accounting').waitFor();
    assert.equal(await panel.getAttribute('data-run-id'), reports[0].run_id);
    const text = await panel.innerText();
    assert(text.includes('结论变更不等于质量提升'));
    assert(text.includes(`$${data.metrics.spent_usd.toFixed(4)}`));
    const changes = data.decisions.filter(item => item.before && item.after && JSON.stringify(item.before) !== JSON.stringify(item.after));
    const correction = changes.filter(item => item.action === 'report_revision').at(-1) ?? changes.filter(item => item.action === 'claim_revised' && item.round > 0).at(-1) ?? changes.at(-1) ?? data.decisions.at(-1);
    if (correction?.before) assert.equal(await panel.locator('.impact-decision.featured .impact-snapshot p').nth(0).textContent(), correction.before.statement);
    if (correction?.after) assert.equal(await panel.locator('.impact-decision.featured .impact-snapshot p').nth(1).textContent(), correction.after.statement);
    if (data.metrics.reused_claims === null) assert(text.includes('暂不推断局部更新收益'));
    else assert.deepEqual((await panel.locator('.impact-reuse strong').allTextContents()).slice(0, 2), [String(data.metrics.reused_claims), String(data.metrics.recomputed_claims)]);
    await page.screenshot({path: path.join(output, '01-report-impact.png'), fullPage: true});
    await page.screenshot({path: path.join(output, '01-report-impact-viewport.png')});
  });
  await step('collaboration source link opens the actual frozen source version', async () => {
    const links = page.locator('.impact-decision.featured .impact-sources button');
    if (!await links.count()) return;
    const title = await links.first().locator('span').innerText();
    await links.first().click();
    await page.getByRole('dialog', {name: '证据详情'}).waitFor();
    assert(title.includes(await page.locator('.drawer-source-title h3').innerText()));
    assert((await page.locator('.source-full-text').innerText()).length > 10);
    await page.screenshot({path: path.join(output, '02-source-provenance.png')});
    await page.getByRole('button', {name: '关闭证据详情'}).click();
  });
  await step('report-version selection never mixes different run histories', async () => {
    for (const report of reports.slice(0, 2).reverse()) {
      await page.getByRole('combobox', {name: '选择报告版本'}).selectOption(report.id);
      await page.locator(`.collaboration-impact[data-run-id="${report.run_id}"] .impact-accounting`).waitFor();
      assert.equal(await page.locator('.collaboration-impact').getAttribute('data-run-id'), report.run_id);
    }
  });
  await step('collaboration view contains actual task timing and role attribution', async () => {
    await page.getByRole('navigation', {name: '工作台导航'}).getByRole('button', {name: '协作进度'}).click();
    await page.locator('.impact-accounting').waitFor();
    const selectedRun = await page.getByRole('combobox', {name: '选择研究记录'}).inputValue();
    const data = await get(`/runs/${selectedRun}/collaboration`);
    assert.equal(await page.locator('.impact-followups li').count(), data.followups.length);
    assert.equal(await page.locator('.impact-details').getAttribute('open'), '');
    await page.screenshot({path: path.join(output, '03-timeline-and-followups.png'), fullPage: true});
  });
  await step('new-run defaults to tested Gemini while keeping Qwen explicit; no run submitted', async () => {
    await page.getByRole('navigation', {name: '工作台导航'}).getByRole('button', {name: '研究概览'}).click();
    await page.getByRole('button', {name: '确认研究大纲', exact: true}).click();
    await page.getByRole('dialog', {name: '确认研究大纲'}).waitFor();
    await page.getByRole('combobox', {name: '运行方式'}).selectOption('live');
    await page.getByRole('button', {name: '高级设置'}).click();
    const models = page.getByRole('combobox', {name: '模型配置'});
    assert.equal(await models.inputValue(), 'gemini-budget');
    assert((await models.innerText()).includes('Qwen · 实验线路'));
    await models.selectOption('qwen-default');
    assert((await page.locator('.outline-model-note').innerText()).includes('Qwen · 实验线路'));
    await page.screenshot({path: path.join(output, '04-model-choice.png')});
    await page.getByRole('button', {name: '稍后开始'}).click();
    await page.getByRole('button', {name: '确认研究大纲', exact: true}).click();
    assert.equal(await page.getByRole('combobox', {name: '模型配置'}).inputValue(), 'gemini-budget');
    await page.getByRole('button', {name: '稍后开始'}).click();
  });
  await step('mobile report and expanded collaboration remain within viewport', async () => {
    await page.getByRole('navigation', {name: '工作台导航'}).getByRole('button', {name: '报告与导出'}).click();
    await page.locator('.impact-accounting').waitFor();
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.locator('.impact-details > summary').click();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({path: path.join(output, '05-mobile-expanded.png'), fullPage: true});
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({path: path.join(output, '05-mobile-viewport.png')});
  });
  await step('unavailable collaboration history leaves report and exports usable (injected network failure)', async () => {
    await page.route('**/api/runs/*/collaboration', route => route.fulfill({status: 503, json: {detail: 'Test-only unavailable history'}}));
    await page.reload();
    await page.getByRole('button', {name: '重新读取', exact: true}).waitFor();
    await page.locator('.report-document').waitFor();
    assert(await page.getByRole('button', {name: '导出 Word'}).isEnabled());
    assert(await page.getByRole('button', {name: '导出 PPT'}).isEnabled());
    await page.unroute('**/api/runs/*/collaboration');
    await page.getByRole('button', {name: '重新读取', exact: true}).click();
    await page.locator('.impact-accounting').waitFor();
  });
  assert.deepEqual(errors, []);
  assert(writes.every(request => request.method === 'POST' && request.url.endsWith('/outline')));
  const runsAfter = await get(`/projects/${project.id}/runs`);
  assert.deepEqual(runsAfter.map(run => [run.id, run.model_profile, run.request_count]), runsBefore.map(run => [run.id, run.model_profile, run.request_count]));
  results.push({name: 'no model/run requests or historical profile changes', status: 'passed'});
} catch (error) {
  process.exitCode = 1;
  results.push({name: 'failure', status: 'failed', error: String(error)});
  console.error(error);
  await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
} finally {
  await writeFile(path.join(output, 'results.json'), JSON.stringify({projectId: project.id, reports: reports.map(report => report.id), checks: results, browserErrors: errors, writeRequests: writes, modelCalls: 0}, null, 2));
  await browser.close();
}
