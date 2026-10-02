"""New source-only reliability diagnostics, separate from the frozen v4 suite.

Expected answers belong to data/evaluation/reliability-v1, never this module's
project input. These targeted cases are diagnostic hypotheses, not held-out proof.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .demo import COMPANIES, DIMENSIONS, DISCLAIMER, demo_sources

SCENARIOS = (
    "challenge-current-conflict", "challenge-faq", "challenge-update-before", "challenge-update-after"
)


def reliability_sources(scenario: str) -> list[dict[str, Any]]:
    """Each architecture gets byte-identical source text and ordering in a pair."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown reliability diagnostic: {scenario}")
    base = {
        "challenge-current-conflict": "test-contradiction",
        "challenge-faq": "test-enterprise-sso",
    }.get(scenario, "dev-default")
    sources = demo_sources(base)
    if scenario == "challenge-update-after":
        security = next(s for s in sources if s["logical_key"] == "senyu-security")
        security.update(
            title=f"{COMPANIES[0]} 十月安全与登录说明",
            published_at="2026-10-01T00:00:00+00:00",
            text=(
                f"{DISCLAIMER}\n资料日期：2026-10-01\n\n"
                "SSO：团队版与企业版均支持 SAML SSO；自 2026-10-01 生效。\n"
                "版本范围：本说明替代 2026-09-15 的登录能力说明；九月时团队版尚不支持，"
                "该历史状态不得当作十月当前能力。\n"
                "权限说明：管理员能够管理成员加入和离开。此处没有给出安全认证、审计结论或跨境合规承诺。"
            ),
        )
    return sources


def seed_reliability(store: Any, scenario: str) -> dict[str, Any]:
    sources = reliability_sources(scenario)
    project = store.create_project({
        "title": f"可靠性诊断 · {scenario}", "mode": "synthetic", "internal": True,
        "question": "比较三家虚构团队知识库的定位、功能、价格、当前 SSO、近期变化和反馈。区分存档与当前事实，保留套餐、生效日期、计费周期及未知项；同日现行来源冲突时明确展示双方。",
        "audience": "产品负责人", "competitors": list(COMPANIES), "dimensions": list(DIMENSIONS),
    })
    store.update_project(project["id"], {"internal": True})
    for source in sources:
        store.add_source(project["id"], source)
    return store.get_project(project["id"])


def apply_reliability_update(store: Any, project_id: str) -> dict[str, Any]:
    """Change exactly one source via Store versioning, preserving old snapshots."""
    replacement = next(
        source for source in reliability_sources("challenge-update-after")
        if source["logical_key"] == "senyu-security"
    )
    return store.add_source(project_id, replacement)


def freeze_reliability_manifest(destination: Path, spec_directory: Path) -> dict[str, Any]:
    manifest = {
        "protocol": "reliability-diagnostics-v1",
        "label": "Targeted post-v4 development diagnostics, not an unseen test set.",
        "scenarios": [],
    }
    for scenario in SCENARIOS:
        sources = reliability_sources(scenario)
        reference = spec_directory / f"{scenario}.json"
        manifest["scenarios"].append({
            "id": scenario, "sources": len(sources),
            "sources_sha256": hashlib.sha256(
                json.dumps(sources, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            "reference_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        })
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if json.loads(destination.read_text(encoding="utf-8")) != manifest:
            raise ValueError("Frozen reliability sources/references changed; choose a new directory.")
    else:
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
