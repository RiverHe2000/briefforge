import {describe, expect, it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {CollaborationContent} from './CollaborationImpact';
import type {CollaborationSummary} from './types';

// Component contract fixtures only. They are never included in the application UI.
const fixture = (): CollaborationSummary => ({
  run_id: 'test-run', mode: 'replay', architecture: 'multi', report_id: 'test-report', history_available: true,
  metrics: {task_count: 3, completed_tasks: 3, followup_tasks: 1, changed_claims: 1, unresolved_claims: 1,
    reused_claims: 5, recomputed_claims: 1, reused_tasks: 0, peak_parallel_research: 2,
    overlap_seconds: 1.5, spent_usd: 0, reserved_usd: 0, request_count: 0},
  decisions: [{id: 'decision', event_id: 7, action: 'claim_corrected', subject: '测试竞品', dimension: 'SSO',
    before: {statement: '旧记录不支持 SSO', status: 'supported', value: null, conditions: []},
    after: {statement: '企业版支持 SSO', status: 'supported', value: null, conditions: ['仅企业版']},
    reason: '新权益说明替换旧版本判断', source_ids: ['new-source'], task_ids: ['followup'],
    roles: ['verifier', 'commercial'], round: 1, created_at: '2026-10-03T00:00:01Z', final_status: 'supported'}],
  followups: [{task_id: 'followup', role: 'commercial', target: '测试竞品', title: '确认套餐范围',
    status: 'completed', round: 1, reason: '旧记录与新资料冲突'}],
  tasks: [{id: 'followup', role: 'commercial', target: '测试竞品', title: '确认套餐范围', status: 'completed',
    round: 1, depends_on: [], reused: false, intervals: [{started_at: '2026-10-03T00:00:00Z', ended_at: '2026-10-03T00:00:02Z', status: 'completed'}]}],
  sources: [{id: 'new-source', title: '企业版权益原文', url: null, published_at: '2026-09-20'}],
  notes: ['组件测试数据'],
});

describe('collaboration evidence presentation', () => {
  it('keeps source-bound corrections, actual roles and billing limits visible together', () => {
    const html = renderToStaticMarkup(<CollaborationContent data={fixture()} onSource={() => {}}/>);
    for (const text of ['旧记录不支持 SSO', '企业版支持 SSO', '仅企业版', '独立核查员', '商业分析员',
      '企业版权益原文', '确认套餐范围', '实际费用', '$0.0000', '固定响应', '结论变更不等于质量提升']) {
      expect(html).toContain(text === '固定响应' ? '回放展示编排机制' : text);
    }
  });
  it('represents missing historical metrics as unknown instead of zero benefits', () => {
    const data = fixture();
    data.history_available = false; data.decisions = []; data.followups = []; data.tasks = [];
    data.metrics.reused_claims = null; data.metrics.recomputed_claims = null;
    data.metrics.peak_parallel_research = null; data.metrics.overlap_seconds = null;
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html).toContain('历史缺失不记为零');
    expect(html).toContain('暂不推断局部更新收益');
    expect(html).not.toContain('跨度');
    expect(html).not.toContain('沿用 <strong>0');
  });
  it('does not turn an unknown action or missing attribution into invented agent work', () => {
    const data = fixture(); data.decisions[0].action = 'future_action'; data.decisions[0].roles = [];
    data.decisions[0].before = null;
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html).toContain('核查记录');
    expect(html).toContain('没有保留这一阶段的结论');
    expect(html).not.toContain('研究协调员');
  });
  it('labels deterministic normalization as automatic checking with no invented actor', () => {
    const data = fixture(); data.decisions[0].action = 'facts_normalized'; data.decisions[0].roles = [];
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html).toContain('自动校验');
    expect(html).not.toContain('独立核查员');
  });
  it('still shows measured task recovery when the older claim-reuse plan is missing', () => {
    const data = fixture(); data.metrics.reused_claims = null; data.metrics.recomputed_claims = null;
    data.metrics.reused_tasks = 2;
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html).toContain('暂不推断局部更新收益');
    expect(html).toContain('恢复时复用 <strong>2</strong> 个已完成任务');
  });
  it('features actual followup changes while still retaining later unknown decisions', () => {
    const data = fixture(); data.decisions[0].action = 'claim_revised';
    data.decisions.push({...data.decisions[0], id: 'unknown', action: 'unknown_retained',
      subject: '其他竞品', after: {...data.decisions[0].after!, statement: '仍然无法确认'}});
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html.indexOf('企业版支持 SSO')).toBeLessThan(html.indexOf('仍然无法确认'));
    expect(html).toContain('保留未知');
    expect(html).toContain('全部 2 条记录');
  });
  it('prefers actual report version differences without claiming model correction or planned reuse', () => {
    const data = fixture(); data.decisions[0].action = 'claim_revised';
    data.metrics.reused_claims = 13; data.metrics.planned_reused_claims = 17;
    data.decisions.unshift({...data.decisions[0], id: 'versions', event_id: null, action: 'report_revision',
      subject: '测试竞品', dimension: '价格', round: 0, roles: [], task_ids: [],
      before: {...data.decisions[0].before!, statement: 'USD 12/席位/月'},
      after: {...data.decisions[0].after!, statement: 'USD 15/席位/月'}});
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    const featured = html.split('impact-details')[0];
    expect(featured).toContain('报告版本变化');
    expect(featured).toContain('上一报告版本');
    expect(featured).toContain('本次报告版本');
    expect(featured).toContain('USD 12/席位/月');
    expect(featured).toContain('USD 15/席位/月');
    expect(featured).not.toContain('商业分析员');
    expect(featured).toContain('本次沿用 <strong>13</strong>');
    expect(featured).not.toContain('本次沿用 <strong>17</strong>');
  });
  it('does not draw verifier intervals as parallel research work', () => {
    const data = fixture(); data.tasks[0].role = 'verifier';
    const html = renderToStaticMarkup(<CollaborationContent data={data} onSource={() => {}}/>);
    expect(html).toContain('尚无完整的研究任务起止记录');
    expect(html).not.toContain('impact-time-row');
  });
});
