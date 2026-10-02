/* One fixed, editable research template. Inputs are a frozen export model only. */
'use strict';
const fs = require('node:fs');
const pptxgen = require('pptxgenjs');
const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error('Expected snapshot and destination paths');
const report = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'BriefForge';
pptx.subject = `${report.disclosure}; report:${report.id}; sha256:${report.fingerprint}`;
pptx.title = report.title || '行业与竞品研究';
pptx.company = 'BriefForge';
pptx.lang = 'zh-CN';
pptx.theme = { headFontFace: 'Microsoft YaHei', bodyFontFace: 'Microsoft YaHei', lang: 'zh-CN' };
const C = { ink: '172B25', green: '275C4C', muted: '64736E', paper: 'FAFAF6', line: 'D9DEDA' };
const base = { fontFace: 'Microsoft YaHei', color: C.ink, margin: 0, breakLine: false, valign: 'top' };

function text(slide, value, x, y, w, h, options = {}) {
  slide.addText(String(value || ''), { ...base, x, y, w, h, fontSize: 19, ...options });
}
function newSlide(title) {
  const slide = pptx.addSlide();
  slide.background = { color: C.paper };
  text(slide, title, .62, .44, 12.08, .72, { fontSize: 31, bold: true });
  text(slide, `BriefForge    ${report.disclosure}    ${report.mode_label}`, .62, 7.07, 10.6, .2, { fontSize: 9, color: C.muted });
  text(slide, String(pptx._slides.length).padStart(2, '0'), 12.02, 7.03, .6, .24, { fontSize: 11, align: 'right', color: C.muted });
  return slide;
}
function sourceNote(claims) {
  const sourceIds = [...new Set(claims.flatMap(c => (c.evidence || []).map(e => e.source_id)))];
  return sourceIds.map(id => {
    const s = report.sources.find(s => s.id === id);
    return s ? `[${String(report.source_numbers[id]).padStart(2, '0')}] ${s.title}; ${s.published_at || '日期未注明'}; ${s.url || '内置虚构资料'}; source:${id}; version:${s.version || 1}` : '';
  }).join('\n');
}
function notes(slide, claims = [], extra = '') {
  const details = claims.map(c => `${c.subject} / ${c.dimension}: ${c.statement}\n状态: ${c.status_label}\n适用条件: ${(c.conditions || []).join('；')}\n证据:\n${(c.evidence || []).map(e => `${e.locator || ''} ${e.quote}`).join('\n')}`).join('\n\n');
  slide.addNotes(`${report.disclosure}\n报告版本 ${report.version || 1}; report:${report.id}; sha256:${report.fingerprint}\n${details}\n${sourceNote(claims)}\n${extra}`);
}
function references(slide, claims, y = 6.53) {
  const numbers = [...new Set(claims.flatMap(c => c.citations || []))].sort((a, b) => a - b);
  text(slide, numbers.length ? `资料来源 ${numbers.map(n => `[${String(n).padStart(2, '0')}]`).join(' ')}    完整引用见备注` : '此页未引用具体产品事实', .66, y, 11.95, .28, { fontSize: 10, color: C.muted });
}
function paragraphBlock(slide, label, body, x, y, w, h, size = 18) {
  text(slide, label, x, y, w, .38, { fontSize: 14, color: C.green, bold: true });
  text(slide, body, x, y + .46, w, h - .46, { fontSize: size, breakLine: false, fit: 'shrink' });
}
const comparison = report.comparison || [];
const allClaims = report.claims || [];

// 1 Cover: purpose, provenance, immutable report identity.
{
  const s = newSlide('行业与竞品研究');
  text(s, report.title || '竞品资料分析', .65, 2.03, 11.45, 1.3, { fontSize: 40, bold: true, color: C.green, fit: 'shrink' });
  text(s, comparison.map(r => r.competitor).join('    '), .68, 3.78, 11.8, .8, { fontSize: 19 });
  text(s, report.synthetic ? '虚构厂商与合成资料演示\n不可作为真实市场或采购结论' : '基于所选公开资料的研究\n结论受资料日期与披露范围限制', .68, 5.08, 11.7, .9, { fontSize: 18, color: C.muted });
  notes(s, [], `报告日期 ${report.created_at || ''}`);
}
// 2 Summary stays verbatim. Long bodies remain completely available in notes.
{
  const s = newSlide('研究结论');
  const summary = report.executive_summary || '此报告尚未形成摘要。已有事实及证据见后续页面。';
  text(s, summary, .68, 1.55, 11.8, 4.98, { fontSize: summary.length > 280 ? 17 : 23, fit: 'shrink', paraSpaceAfterPt: 5 });
  text(s, `冻结资料 ${report.sources.length} 份    研究事实 ${allClaims.length} 条    报告版本 ${report.version || 1}`, .68, 6.62, 11.8, .28, { fontSize: 12, color: C.muted });
  notes(s, [], summary);
}
// 3 Native comparison table: keep positioning distinct from unsupported rankings.
{
  const s = newSlide('产品定位比较');
  const rows = [[{ text: '产品', options: { bold: true, color: 'FFFFFF', fill: C.green } }, { text: '定位与目标场景', options: { bold: true, color: 'FFFFFF', fill: C.green } }], ...comparison.map(r => [r.competitor, r.positioning || '未形成定位结论'])];
  s.addTable(rows, { x: .68, y: 1.64, w: 11.9, colW: [3.15, 8.75], rowH: .9, border: { type: 'solid', color: C.line, pt: .6 }, fill: 'FFFFFF', color: C.ink, fontFace: 'Microsoft YaHei', fontSize: 19, margin: .16, valign: 'middle', autoPage: false, bold: false });
  // Explicit first-row styling keeps tables native and editable.
  const claims = allClaims.filter(c => c.dimension === '定位');
  references(s, claims);
  notes(s, claims);
}
// 4–6 Three competitor profiles. Additional facts are retained verbatim in notes.
for (let i = 0; i < 3; i++) {
  const row = comparison[i];
  const subject = row ? row.competitor : '';
  const s = newSlide(subject || '产品资料待补充');
  const claims = allClaims.filter(c => c.subject === subject);
  const picks = ['定位', '核心功能', '近期变化', '用户反馈'].map(d => claims.find(c => c.dimension === d)).filter(Boolean);
  if (!row) text(s, '当前报告没有更多竞品资料。', .68, 2.0, 11.7, 1, { fontSize: 24, color: C.muted });
  else if (!picks.length) text(s, row.positioning || '当前报告没有更多已整理的产品事实。', .68, 1.8, 11.7, 3.5, { fontSize: 21, fit: 'shrink' });
  picks.forEach((c, j) => {
    const conditions = (c.conditions || []).filter(value => value !== c.statement);
    const body = c.statement + (conditions.length ? '\n适用条件：' + conditions.join('；') : '');
    paragraphBlock(s, `${c.dimension}    ${c.status_label}`, body, .7, 1.48 + j * 1.19, 11.8, 1.09, 17);
  });
  references(s, claims);
  notes(s, claims);
}
// 7 Native chart and exact pricing conditions share the Word report's data.
{
  const s = newSlide(report.chart.title);
  const chart = report.chart;
  s.addChart(pptx.ChartType.bar, [{ name: chart.series, labels: chart.labels, values: chart.values }], {
    x: .65, y: 1.55, w: 5.35, h: 3.82, catAxisLabelFontFace: 'Microsoft YaHei', catAxisLabelFontSize: 12,
    valAxisLabelFontSize: 12, valAxisMinVal: 0, showLegend: false, showTitle: false, showValue: true,
    showCatName: false, showSerName: false, chartColors: [C.green], showBorder: false,
    showCatName: false, catAxisLineColor: C.line, valAxisLineColor: C.line, valGridLine: { color: C.line },
    showValue: true, dataLabelFormatCode: chart.kind === 'price' ? '0.00' : '0', dataLabelPosition: 'outEnd',
    showMarker: false, showLine: false, showCatName: false, showPercent: false,
    valAxisTitle: chart.series, showValAxisTitle: true, showCatName: false,
    catAxisLabelRotate: 0, showLegend: false, barDir: 'col',
  });
  // Pricing may include several verbatim billing-condition lines. Reserve real
  // body height instead of relying on PowerPoint's optional shrink-on-edit.
  const priceRowStep = 4.68 / Math.max(3, comparison.length);
  comparison.forEach((row, i) => {
    const y = 1.5 + i * priceRowStep;
    text(s, row.competitor, 6.38, y, 6.22, .27, { fontSize: 14, color: C.green, bold: true });
    text(s, row.price || '未形成价格结论', 6.38, y + .33, 6.22, priceRowStep - .37, { fontSize: comparison.length > 3 ? 14 : 17, breakLine: false, fit: 'shrink' });
  });
  text(s, chart.note, .68, 6.29, 11.92, .31, { fontSize: 12, color: C.muted, fit: 'shrink' });
  const claims = allClaims.filter(c => c.dimension === '价格');
  references(s, claims, 6.7);
  notes(s, claims, JSON.stringify(chart));
}
// 8 SSO conditions must stay visible; speaker notes carry every exact evidence quote.
{
  const s = newSlide('SSO 能力与套餐条件');
  comparison.forEach((row, i) => paragraphBlock(s, row.competitor, row.sso || '未形成 SSO 结论', .7, 1.55 + i * 1.55, 11.8, 1.39, 21));
  const claims = allClaims.filter(c => c.dimension === 'SSO');
  references(s, claims);
  notes(s, claims);
}
// 9 Unresolved findings are not rewritten into confident conclusions.
{
  const s = newSlide('待确认事项与研究限制');
  const unresolved = report.unresolved || [];
  const lines = unresolved.length ? unresolved : ['此报告未列出额外待确认事项。每项结论仍受来源日期与披露范围限制。'];
  text(s, lines.join('\n\n'), .7, 1.58, 11.82, 4.65, { fontSize: lines.join('').length > 400 ? 18 : 21, fit: 'shrink' });
  notes(s, allClaims.filter(c => c.status !== 'supported'), `待确认事项\n${lines.join('\n')}\n本版本变化\n${(report.changes || []).join('\n')}`);
}
// 10 Full provenance and an embedded shared manifest in speaker notes.
{
  const s = newSlide('资料来源与报告版本');
  const visible = report.sources.slice(0, 12).map(source => `[${String(report.source_numbers[source.id]).padStart(2, '0')}] ${source.title}`);
  text(s, visible.join('\n'), .7, 1.52, 11.83, 4.55, { fontSize: 16, breakLine: false, paraSpaceAfterPt: 6, fit: 'shrink' });
  text(s, `全部 ${report.sources.length} 项来源及逐条证据见演讲者备注和 Word 附录。报告版本 ${report.version || 1}。`, .7, 6.2, 11.83, .45, { fontSize: 12, color: C.muted });
  notes(s, allClaims, `BRIEFFORGE_SNAPSHOT_BEGIN\n${JSON.stringify(report.manifest)}\nBRIEFFORGE_SNAPSHOT_END`);
}
if (pptx._slides.length !== 10) throw new Error('The research template must contain ten slides');
pptx.writeFile({ fileName: outputPath });
