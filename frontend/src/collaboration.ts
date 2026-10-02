export type TimedTask = {
  task_id: string;
  role: string;
  title: string;
  started_at: string | null;
  finished_at: string | null;
};

/** Closed intervals only: queued or incomplete tasks cannot prove overlap. */
export function observedTimeline(tasks: TimedTask[]) {
  const measured = tasks.flatMap(task => {
    const start = task.started_at ? Date.parse(task.started_at) : NaN;
    const end = task.finished_at ? Date.parse(task.finished_at) : NaN;
    return Number.isFinite(start) && Number.isFinite(end) && end >= start
      ? [{...task, start, end, elapsedMs: end - start}]
      : [];
  }).sort((a, b) => a.start - b.start);
  if (!measured.length) return {bars: [], spanMs: 0, peak: 0, omitted: tasks.length};
  const first = Math.min(...measured.map(task => task.start));
  const last = Math.max(...measured.map(task => task.end));
  const spanMs = last - first;
  // End events sort first, so adjacent tasks are not reported as parallel.
  const edges = measured.filter(task => task.end > task.start)
    .flatMap(task => [{at: task.start, delta: 1}, {at: task.end, delta: -1}])
    .sort((a, b) => a.at - b.at || a.delta - b.delta);
  let concurrent = 0, peak = 0;
  for (const event of edges) {
    concurrent += event.delta;
    peak = Math.max(peak, concurrent);
  }
  return {
    bars: measured.map(task => ({...task, offset: spanMs ? (task.start - first) / spanMs * 100 : 0,
      width: spanMs ? task.elapsedMs / spanMs * 100 : 0})),
    spanMs, peak, omitted: tasks.length - measured.length,
  };
}

export function elapsedLabel(ms: number) {
  if (!Number.isFinite(ms) || ms < 0) return '未记录';
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} 秒`;
  return `${Math.floor(ms / 60000)} 分 ${Math.round(ms % 60000 / 1000)} 秒`;
}

export function featuredDecision(decisions: CollaborationDecision[]) {
  const changes = decisions.filter(item => item.before && item.after && JSON.stringify(item.before) !== JSON.stringify(item.after));
  return changes.filter(item => item.action === 'report_revision').at(-1)
    ?? changes.filter(item => item.action === 'claim_revised' && item.round > 0).at(-1)
    ?? changes.at(-1) ?? decisions.at(-1);
}

export const agentRole = (role: string) => ({coordinator: '研究协调员', industry: '行业研究员',
  competitor: '竞品研究员', commercial: '商业分析员', verifier: '独立核查员', editor: '报告编辑员',
  researcher: '竞品研究员', verify: '独立核查员', report: '报告编辑员', planner: '研究协调员',
  analyst: '商业分析员', business: '商业分析员', single: '单 Agent 研究员'}[role] ?? role);
import type {CollaborationDecision} from './types';

