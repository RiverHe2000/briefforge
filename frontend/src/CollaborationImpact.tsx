import {useEffect, useState} from 'react';
import {ArrowDown, ArrowUpRight, ChevronDown, Clock3, GitBranch, Layers3, Link2, Loader2, Network, RefreshCw, ShieldCheck, Wallet} from 'lucide-react';
import {api} from './api';
import {agentRole, elapsedLabel, featuredDecision, observedTimeline} from './collaboration';
import {day, money, statusLabel} from './utils';
import type {CollaborationClaim, CollaborationDecision, CollaborationSummary} from './types';
import './collaboration.css';

const researchRoles = new Set(['industry', 'competitor', 'commercial', 'single']);
const actionLabels: Record<string, string> = {
  corrected: '结论修正', claim_corrected: '结论修正', correction: '结论修正',
  resolved: '核查处理', uncertain: '保留未知', unresolved: '保留未知',
  followup: '请求补查', verified: '核查记录', supported: '证据核查',
  facts_normalized: '自动校验', claim_revised: '结论变更',
  followup_requested: '请求补查', unknown_retained: '保留未知',
  report_revision: '报告版本变化',
};

export function CollaborationImpact({runId, running=false, detailed=false, onSource}: {
  runId: string; running?: boolean; detailed?: boolean; onSource: (sourceId: string) => void;
}) {
  const [state, setState] = useState<{runId: string; data: CollaborationSummary | null; failed: boolean}>({runId, data: null, failed: false});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false, pending = false;
    setState({runId, data: null, failed: false});
    const refresh = async () => {
      if (pending || disposed) return;
      pending = true;
      try {
        const data = await api<CollaborationSummary>(`/runs/${runId}/collaboration`);
        if (!disposed && data.run_id === runId) setState({runId, data, failed: false});
      } catch {
        if (!disposed) setState(old => ({runId, data: old.runId === runId ? old.data : null, failed: true}));
      } finally {pending = false;}
    };
    void refresh();
    const timer = running ? window.setInterval(refresh, 3000) : undefined;
    return () => {disposed = true; if (timer) window.clearInterval(timer);};
  }, [runId, running, retry]);

  const data = state.runId === runId ? state.data : null;
  return <section className="collaboration-impact" aria-label="协作带来的变化" data-run-id={runId}>
    <header className="impact-heading"><div className="impact-title"><span className="impact-icon"><GitBranch size={20}/></span><div><span className="impact-eyebrow">EVIDENCE OF COLLABORATION</span><h2>协作带来的变化</h2></div></div>{data && <span className={`impact-mode ${data.mode}`}>{data.mode === 'replay' ? '固定响应回放' : '真实运行记录'}</span>}</header>
    {!data ? <div className="impact-loading">{state.failed ? <><span>暂时无法读取这次研究的协作记录。</span><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={14}/>重新读取</button></> : <><Loader2 size={16} className="spin"/><span>正在读取核查与任务记录…</span></>}</div> : <CollaborationContent data={data} detailed={detailed} onSource={onSource}/>}
    {data && state.failed && <p className="impact-stale">实时更新暂时中断，当前显示上次读取的持久化记录。</p>}
  </section>;
}

export function CollaborationContent({data, detailed=false, onSource}: {data: CollaborationSummary; detailed?: boolean; onSource: (sourceId: string) => void}) {
  const metrics = data.metrics;
  const featured = featuredDecision(data.decisions);
  const remaining = data.decisions.filter(item => item.id !== featured?.id);
  const measured = observedTimeline(data.tasks.filter(task => researchRoles.has(task.role)).flatMap(task => task.intervals.map((interval, index) => ({
    task_id: `${task.id}:${index}`, role: task.role, title: task.title,
    started_at: interval.started_at, finished_at: interval.ended_at,
  }))));
  return <>
    <p className="impact-intro">{data.architecture === 'single' ? '单 Agent 基线的实际执行记录。' : data.architecture === 'pipeline' ? '固定流水线的实际执行记录。' : '从初步判断到补查结果，每次变化都能追溯到任务和来源。'}{data.mode === 'replay' && ' 回放展示编排机制，不代表真实模型能力。'}</p>
    <div className="impact-metrics">
      <ImpactMetric icon={<GitBranch size={15}/>} value={data.history_available ? metrics.changed_claims : null} label="结论发生变化" unit="项"/>
      <ImpactMetric icon={<ShieldCheck size={15}/>} value={data.history_available ? metrics.followup_tasks : null} label="实际补查任务" unit="项"/>
      <ImpactMetric icon={<Layers3 size={15}/>} value={metrics.reused_claims} label="沿用已有结论" unit="项"/>
      <ImpactMetric icon={<Network size={15}/>} value={metrics.peak_parallel_research} label="观察到的并行峰值" unit="个研究任务"/>
    </div>
    {!data.history_available && <p className="impact-empty">这次研究未保留足够的核查或计时记录，无法还原协作过程。历史缺失不记为零。</p>}
    {featured ? <DecisionCard decision={featured} sources={data.sources} onSource={onSource} featured/> : data.history_available && <p className="impact-empty">{metrics.followup_tasks > 0 ? '已安排补查，尚未记录可展示的结论变化。' : '本次没有记录到结论修正，保留实际研究结果。'}</p>}
    <div className="impact-reuse"><RefreshCw size={15}/><p>{metrics.reused_claims !== null && metrics.recomputed_claims !== null ? <>本次沿用 <strong>{metrics.reused_claims}</strong> 项结论，按计划重查 <strong>{metrics.recomputed_claims}</strong> 项。</> : '这次记录未包含结论复用与重算数量，暂不推断局部更新收益。'}{metrics.reused_tasks > 0 && <>恢复时复用 <strong>{metrics.reused_tasks}</strong> 个已完成任务。</>}</p></div>
    <details className="impact-details" open={detailed || undefined}>
      <summary><span>查看分工、全部 {data.decisions.length} 条记录与执行时间线</span><ChevronDown size={16}/></summary>
      {remaining.length > 0 && <div className="impact-history"><h3>其余核查记录 <span>{remaining.length}</span></h3>{remaining.map(decision => <DecisionCard key={decision.id} decision={decision} sources={data.sources} onSource={onSource}/>)}</div>}
      <div className="impact-detail-columns">
        <section className="impact-followups"><h3>补查分工 <span>{data.followups.length}</span></h3>{data.followups.length ? <ol>{data.followups.map(task => <li key={task.task_id}><div><span className="impact-role">{agentRole(task.role)}</span><span className="impact-task-state">{statusLabel(task.status)}</span></div><h4>{task.title}</h4><p>{task.reason || '这项任务未记录补查原因。'}</p><small>{task.target || '跨资料核查'} · 第 {task.round} 轮补查</small></li>)}</ol> : <p className="impact-empty">没有新增补查任务。</p>}</section>
        <section className="impact-timeline"><h3>实际执行时间线 <Clock3 size={14}/></h3><p className="impact-timing-note">按已记录的研究任务起止时间绘制。{metrics.overlap_seconds !== null && <>并行重叠时长：<strong>{elapsedLabel(metrics.overlap_seconds * 1000)}</strong>。</>}不含协调、核查和编辑任务。</p>{measured.bars.length ? <><div className="impact-time-axis"><span>研究任务开始</span><span>跨度 {elapsedLabel(measured.spanMs)}</span></div><div className="impact-time-rows">{measured.bars.map(task => <div className="impact-time-row" key={task.task_id}><div><span>{agentRole(task.role)}</span><strong title={task.title}>{task.title}</strong><small>{elapsedLabel(task.elapsedMs)}</small></div><div className="impact-time-track" aria-label={`${task.title}，${elapsedLabel(task.elapsedMs)}`}><i className={`role-${task.role}`} style={{left: `${task.offset}%`, width: `${task.width}%`}}/></div></div>)}</div></> : <p className="impact-empty">尚无完整的研究任务起止记录，不能判断并行程度。</p>}{measured.omitted > 0 && <p className="impact-timing-note">另有 {measured.omitted} 个执行片段缺少有效结束时间，未绘入时间线。</p>}</section>
      </div>
    </details>
    <footer className="impact-accounting"><div><Wallet size={15}/><span>实际费用 <strong>{money(metrics.spent_usd)}</strong></span><span>预留 <strong>{money(metrics.reserved_usd)}</strong></span><span>{metrics.request_count} 次模型请求</span></div><p>仍有 {metrics.unresolved_claims} 项结论待确认。结论变更不等于质量提升；并行时间不等于节省时间。计划重查范围与实际任务、模型请求分别记录。</p>{data.notes.length > 0 && <ul>{data.notes.map((note, index) => <li key={index}>{note}</li>)}</ul>}</footer>
  </>;
}

function ImpactMetric({icon, value, label, unit}: {icon: React.ReactNode; value: number | null; label: string; unit: string}) {
  return <div className="impact-metric"><span>{icon}{label}</span><strong>{value ?? '—'}<small>{value === null ? '未记录' : unit}</small></strong></div>;
}

function DecisionCard({decision, sources, onSource, featured=false}: {decision: CollaborationDecision; sources: CollaborationSummary['sources']; onSource: (sourceId: string) => void; featured?: boolean}) {
  return <article className={`impact-decision ${featured ? 'featured' : ''}`}>
    <header><div><span>{actionLabels[decision.action] ?? '核查记录'}</span><h3>{[decision.subject, decision.dimension].filter(Boolean).join(' · ') || '研究结论'}</h3></div>{decision.final_status && <span className={`impact-status ${decision.final_status}`}>{statusLabel(decision.final_status)}</span>}</header>
    {decision.roles.length > 0 && <div className="impact-roles">{decision.roles.map(role => <span key={role}>{agentRole(role)}</span>)}{decision.round > 0 && <small>第 {decision.round} 轮</small>}</div>}
    <div className="impact-before-after"><ClaimSnapshot claim={decision.before} label={decision.action === 'report_revision' ? '上一报告版本' : '核查前记录'}/><span className="impact-change-arrow"><ArrowDown size={17}/></span><ClaimSnapshot claim={decision.after} label={decision.action === 'report_revision' ? '本次报告版本' : '核查后记录'}/></div>
    {decision.reason && <p className="impact-reason"><ShieldCheck size={14}/><span>{decision.reason}</span></p>}
    {decision.source_ids.length > 0 && <div className="impact-sources">{decision.source_ids.map(id => {const source = sources.find(item => item.id === id);return <button key={id} onClick={() => onSource(id)}><Link2 size={13}/><span>{source?.title ?? '查看所用来源'}{source && <small>{day(source.published_at)}</small>}</span><ArrowUpRight size={12}/></button>;})}</div>}
  </article>;
}

function ClaimSnapshot({claim, label}: {claim: CollaborationClaim | null; label: string}) {
  return <div className="impact-snapshot"><span>{label}{claim?.status && <small>{statusLabel(claim.status)}</small>}</span><p>{claim?.statement || '没有保留这一阶段的结论。'}</p>{claim?.conditions.length ? <small>适用条件：{claim.conditions.join('；')}</small> : null}</div>;
}
