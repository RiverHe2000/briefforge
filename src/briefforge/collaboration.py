"""Read-only collaboration evidence derived from durable events, never a sales score."""

from __future__ import annotations

from datetime import UTC, datetime

RESEARCH_ROLES = {"industry", "competitor", "commercial", "single"}


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
    except (TypeError, ValueError):
        return None


def _snapshot(value):
    if value is None:
        return None
    if isinstance(value, str):
        return {"statement": value, "status": None, "value": None, "conditions": []}
    return {
        "statement": str(value.get("statement", "")),
        "status": value.get("status"),
        "value": value.get("value"),
        "conditions": value.get("conditions") or [],
    }


def _all_events(store, run_id):
    rows, cursor = [], 0
    while batch := store.list_events(run_id, cursor):
        rows.extend(batch)
        cursor = batch[-1]["id"]
    return rows


def _claim_fields(claim):
    return {
        key: claim.get(key)
        for key in ("statement", "status", "value", "conditions", "evidence", "computation")
    }


def _version_comparison(store, run, report, reuse_plan):
    """Compare frozen reports; a planned reuse is not proof of unchanged output."""
    if not report or not reuse_plan:
        return None, [], []
    earlier = [r for r in store.list_reports(run["project_id"]) if r["version"] < report["version"]]
    if not earlier:
        return None, [], []
    prior = max(earlier, key=lambda r: r["version"])
    previous = {f"{c['subject']}::{c['dimension']}": c for c in prior.get("claims", [])}
    current = {f"{c['subject']}::{c['dimension']}": c for c in report.get("claims", [])}
    planned = set(reuse_plan.get("preserved_claim_keys", []))
    unchanged = sum(
        key in previous and key in current and _claim_fields(previous[key]) == _claim_fields(current[key])
        for key in planned
    )
    changes = []
    referenced = set()
    for key in previous.keys() & current.keys():
        old, new = previous[key], current[key]
        if _claim_fields(old) == _claim_fields(new):
            continue
        evidence_ids = list(dict.fromkeys(e["source_id"] for c in (old, new) for e in c.get("evidence", [])))
        referenced.update(evidence_ids)
        changes.append(
            {
                "id": f"version:{prior['id']}:{report['id']}:{key}",
                "event_id": None,
                "action": "report_revision",
                "subject": new["subject"],
                "dimension": new["dimension"],
                "before": _snapshot(old),
                "after": _snapshot(new),
                "reason": f"报告 v{prior['version']} → v{report['version']} 的冻结版本对比；展示资料更新后的最终变化，不代表模型核查前后的草稿。",
                "source_ids": evidence_ids,
                "task_ids": [],
                "roles": [],
                "round": 0,
                "created_at": report["created_at"],
                "final_status": new.get("status"),
            }
        )
    old_sources = [s for s in prior.get("sources", []) if s["id"] in referenced]
    return unchanged, sorted(changes, key=lambda d: (d["subject"], d["dimension"])), old_sources


def collaboration_summary(store, run_id):
    run = store.get_run(run_id)
    tasks = store.list_tasks(run_id)
    by_id = {task["id"]: task for task in tasks}
    events = _all_events(store, run_id)
    report = store.get_report(run["report_id"]) if run.get("report_id") else None
    claims = report.get("claims", []) if report else []
    final = {(c["subject"], c["dimension"]): c for c in claims}
    sources = (
        list(report.get("sources", []))
        if report
        else [store.get_source(source_id) for source_id in run.get("source_ids", [])]
    )
    source_ids = {source["id"] for source in sources}
    new_decisions = [event for event in events if event["type"] == "evidence_decision"]
    # Revisions can cite immutable prior versions, but never another workspace.
    referenced = {
        sid
        for event in events
        if event["type"] in {"evidence_decision", "claim_corrected"}
        for sid in event["payload"].get("source_ids", [])
    }
    for sid in sorted(referenced - source_ids):
        try:
            source = store.get_source(sid)
        except KeyError:
            continue
        if source["project_id"] == run["project_id"]:
            sources.append(source)
            source_ids.add(sid)
    # Old records can expose their recorded corrections without pretending
    # that their missing status, actor or reuse history has been measured.
    decision_events = new_decisions or [event for event in events if event["type"] == "claim_corrected"]
    decisions, seen = [], set()
    for event in decision_events:
        data = event["payload"]
        decision_id = data.get("decision_id") or str(event["id"])
        if decision_id in seen:
            continue
        seen.add(decision_id)
        task_ids = [tid for tid in data.get("task_ids", []) if tid in by_id]
        evidence_ids = data.get("source_ids", []) or [item["source_id"] for item in data.get("evidence", [])]
        current = final.get((data.get("subject"), data.get("dimension")))
        decisions.append(
            {
                "id": decision_id,
                "event_id": event["id"],
                "action": data.get("action", "claim_revised"),
                "subject": data.get("subject", ""),
                "dimension": data.get("dimension", ""),
                "before": _snapshot(data.get("before")),
                "after": _snapshot(data.get("after")),
                "reason": data.get("reason") or event["message"],
                "source_ids": list(dict.fromkeys(sid for sid in evidence_ids if sid in source_ids)),
                "task_ids": task_ids,
                "roles": list(dict.fromkeys(by_id[tid]["role"] for tid in task_ids)),
                "round": data.get("round", 0),
                "created_at": event["created_at"],
                "final_status": current.get("status") if current else None,
            }
        )

    intervals = {tid: [] for tid in by_id}
    open_intervals, reused_ids = {}, set()
    followup_reasons = {}
    reuse_plan = None
    for event in events:
        data, kind = event["payload"], event["type"]
        tid = data.get("task_id")
        if kind == "task_started" and tid in by_id:
            if tid in open_intervals:
                # A new start without a completion may be a crashed attempt.
                # Retain it as interrupted; do not invent its end time.
                open_intervals[tid]["status"] = "interrupted"
            interval = {"started_at": event["created_at"], "ended_at": None, "status": "running"}
            intervals[tid].append(interval)
            open_intervals[tid] = interval
        elif kind in {"task_completed", "task_failed"} and tid in open_intervals:
            interval = open_intervals.pop(tid)
            interval.update(ended_at=event["created_at"], status=kind.removeprefix("task_"))
        elif kind == "task_reused" and tid in by_id:
            reused_ids.add(tid)
        elif kind == "reuse_planned":
            reuse_plan = data
        elif kind == "followup_planned":
            for spec in data.get("tasks", []):
                followup_reasons[(data.get("round", 0), spec.get("target"), spec.get("role"))] = spec.get(
                    "question"
                ) or (spec.get("issue") or {}).get("reason", "")
    if run["status"] not in {"queued", "running"}:
        for interval in open_intervals.values():
            interval["status"] = "interrupted"

    verified_reuse, version_changes, prior_sources = _version_comparison(store, run, report, reuse_plan)
    decisions.extend(version_changes)
    for source in prior_sources:
        if source["id"] not in source_ids:
            sources.append(source)
            source_ids.add(source["id"])

    # Measure completed research intervals only. Overlap is observed concurrency,
    # not a counterfactual prediction of time saved against another architecture.
    points = {}
    for tid, records in intervals.items():
        if by_id[tid]["role"] not in RESEARCH_ROLES:
            continue
        for interval in records:
            start, end = _timestamp(interval["started_at"]), _timestamp(interval["ended_at"])
            if start is not None and end is not None and end > start:
                points[start] = points.get(start, 0) + 1
                points[end] = points.get(end, 0) - 1
    active, peak, overlap, last = 0, 0, 0.0, None
    for point in sorted(points):
        if last is not None and active >= 2:
            overlap += point - last
        active += points[point]
        peak = max(peak, active)
        last = point

    changed = {
        (d["subject"], d["dimension"])
        for d in decisions
        if d["before"] and d["after"] and d["before"] != d["after"]
    }
    followups = [
        {
            "task_id": t["id"],
            "role": t["role"],
            "target": t["target"],
            "title": t["title"],
            "status": t["status"],
            "round": t["round"],
            "reason": followup_reasons.get((t["round"], t["target"], t["role"]), t["title"]),
        }
        for t in tasks
        if t.get("round", 0) > 0 and t["role"] in RESEARCH_ROLES
    ]
    notes = [
        "修订次数表示结论发生变化，不等于新增正确答案；请核对最终状态与原文。",
        "并行重叠来自已结束研究任务的起止事件，不代表相对单 Agent 的节省时间。",
    ]
    if run["mode"] == "replay":
        notes.append("此记录为固定响应回放，只证明协作机制，不代表真实模型质量或性能。")
    if not new_decisions:
        notes.append("此运行未记录完整决策轨迹；历史记录缺少的状态和分工不会补造。")
    if reuse_plan is None:
        notes.append("此运行没有记录局部更新的复用计划；复用数量显示为未记录。")
    else:
        notes.append(
            "沿用数量按相邻冻结报告的正文、状态、值、条件、引文和计算字段完全一致计算；计划复用不等于最终保持不变。"
        )
    return {
        "run_id": run_id,
        "mode": run["mode"],
        "architecture": run["architecture"],
        "report_id": run.get("report_id"),
        "history_available": bool(decisions or any(intervals.values())),
        "metrics": {
            "task_count": len(tasks),
            "completed_tasks": sum(t["status"] == "completed" for t in tasks),
            "followup_tasks": len(followups),
            "changed_claims": len(changed),
            "unresolved_claims": sum(c.get("status") != "supported" for c in claims),
            "reused_claims": verified_reuse,
            "planned_reused_claims": len(set(reuse_plan.get("preserved_claim_keys", [])))
            if reuse_plan
            else None,
            "recomputed_claims": len(set(reuse_plan.get("affected_claim_keys", []))) if reuse_plan else None,
            "reused_tasks": len(reused_ids),
            "peak_parallel_research": peak if points else None,
            "overlap_seconds": round(overlap, 3) if points else None,
            "spent_usd": run["spent_usd"],
            "reserved_usd": run["reserved_usd"],
            "request_count": run["request_count"],
        },
        "decisions": decisions,
        "followups": followups,
        "tasks": [
            {
                **{key: t[key] for key in ("id", "role", "target", "title", "status", "round", "depends_on")},
                "intervals": intervals[t["id"]],
                "reused": t["id"] in reused_ids,
            }
            for t in tasks
        ],
        "sources": [
            {"id": s["id"], "title": s["title"], "url": s.get("url"), "published_at": s.get("published_at")}
            for s in sources
        ],
        "notes": notes,
    }
