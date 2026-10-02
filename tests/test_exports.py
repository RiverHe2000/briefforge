import json
import re
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from briefforge.demo import COMPANIES, demo_sources
from briefforge.export_contract import EXPORT_RENDERER_VERSION
from briefforge.exports import build_export_model, export_report


def sample_report():
    sources = [
        {"id": f"s{i}", "version": 1, "sha256": f"hash-{i}", **source}
        for i, source in enumerate(demo_sources())
    ]
    claims = []
    mapping = {
        "定位": "product",
        "核心功能": "product",
        "价格": "pricing",
        "SSO": "security",
        "近期变化": "release",
        "用户反馈": "feedback",
    }
    for company in COMPANIES:
        for dimension, kind in mapping.items():
            source = next(s for s in sources if s["competitor"] == company and s["kind"] == kind)
            quote = next(line for line in source["text"].splitlines() if line.startswith(dimension + "："))
            statement = quote.split("：", 1)[1]
            claims.append(
                {
                    "id": f"c{len(claims)}",
                    "subject": company,
                    "dimension": dimension,
                    "statement": statement,
                    "status": "uncertain" if "未披露" in statement else "supported",
                    "evidence": [{"source_id": source["id"], "quote": quote, "locator": dimension}],
                    "conditions": [],
                    "value": statement,
                    "computation": None,
                }
            )
    comparison = []
    for company in COMPANIES:
        selected = [c for c in claims if c["subject"] == company]
        by_dimension = {c["dimension"]: c["statement"] for c in selected}
        comparison.append(
            {
                "competitor": company,
                "positioning": by_dimension["定位"],
                "price": by_dimension["价格"],
                "sso": by_dimension["SSO"],
                "conditions": [],
                "claim_ids": [c["id"] for c in selected],
            }
        )
    return {
        "id": "sample-report",
        "version": 1,
        "title": "团队知识库 SaaS 竞品研究",
        "executive_summary": "三家虚构产品针对不同团队场景。报价采用各自披露的套餐与计费周期；SSO 需保留企业版条件，未披露能力继续待确认。",
        "sections": [
            {
                "id": "limits",
                "heading": "比较口径",
                "body": "单条体验不能代表用户总体。当前资料不支持市场份额或实测生产率的比较。",
                "claim_ids": [],
            }
        ],
        "comparison": comparison,
        "claims": claims,
        "sources": sources,
        "unresolved": ["星河文库的 SAML SSO 能力未披露。", "企业合同报价与实际集成表现需要进一步确认。"],
        "changes": [],
        "synthetic": True,
        "mode": "replay",
        "created_at": "2026-10-02T00:00:00+00:00",
        "content_hash": "a" * 64,
    }


def _text(archive, name):
    return "".join(ET.fromstring(archive.read(name)).itertext())


def test_export_does_not_mutate_snapshot_and_embeds_editable_evidence(tmp_path):
    report = sample_report()
    before = deepcopy(report)
    document = export_report(report, "docx", tmp_path)
    slides = export_report(report, "pptx", tmp_path)
    assert report == before
    with ZipFile(document) as archive:
        content = _text(archive, "word/document.xml")
        assert all(row["price"] in content and row["sso"] in content for row in report["comparison"])
        assert "虚构测试资料" in _text(archive, "word/header1.xml")
        assert "虚构测试资料" in _text(archive, "word/footer1.xml")
        assert "word/charts/chart1.xml" in archive.namelist()
        assert "word/embeddings/chart-data.xlsx" in archive.namelist()
        assert b"<w:tbl>" in archive.read("word/document.xml")
        assert len(archive.namelist()) == len(set(archive.namelist()))
        doc_manifest = json.loads(ET.fromstring(archive.read("customXml/briefforge-snapshot.xml")).text)
    with ZipFile(slides) as archive:
        slide_files = sorted(n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        assert len(slide_files) == 10
        for name in slide_files:
            ids = [
                node.attrib["id"]
                for node in ET.fromstring(archive.read(name)).iter()
                if node.tag.endswith("}cNvPr")
            ]
            assert len(ids) == len(set(ids)), "Native shapes must not duplicate text shape IDs"
        content_types = ET.fromstring(archive.read("[Content_Types].xml"))
        assert all(
            node.attrib["PartName"].lstrip("/") in archive.namelist()
            for node in content_types
            if node.tag.endswith("}Override")
        )
        assert all("虚构测试资料" in _text(archive, name) for name in slide_files)
        content = "\n".join(_text(archive, name) for name in slide_files)
        assert all(row["price"] in content and row["sso"] in content for row in report["comparison"])
        assert "ppt/charts/chart1.xml" in archive.namelist()
        assert any(
            name.startswith("ppt/embeddings/") and name.endswith(".xlsx") for name in archive.namelist()
        )
        all_notes = "\n".join(
            _text(archive, n)
            for n in archive.namelist()
            if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", n)
        )
        match = re.search(
            r"BRIEFFORGE_SNAPSHOT_BEGIN\s*(.*?)\s*BRIEFFORGE_SNAPSHOT_END", all_notes, re.DOTALL
        )
        assert match
        deck_manifest = json.loads(match.group(1))
    assert doc_manifest == deck_manifest
    assert doc_manifest["exporter_version"] == EXPORT_RENDERER_VERSION
    assert doc_manifest["model_profile"] == "qwen-default"
    assert len(doc_manifest["claims"]) == 18


def test_invalid_quote_and_hidden_synthetic_source_refused(tmp_path):
    report = sample_report()
    report["claims"][0]["evidence"][0]["quote"] = "this phrase never appeared"
    with pytest.raises(ValueError, match="冻结资料"):
        export_report(report, "docx", tmp_path)
    report = sample_report()
    report["synthetic"] = False
    with pytest.raises(ValueError, match="合成来源"):
        build_export_model(report)


def test_multiline_price_conditions_survive_visible_slide_and_shared_manifest(tmp_path):
    report = sample_report()
    for row in report["comparison"]:
        source = next(s for s in report["sources"] if s["competitor"] == row["competitor"] and s["kind"] == "pricing")
        details = "\n".join(line for line in source["text"].splitlines() if line.startswith(("价格：", "计费说明：")))
        row["price"] = details
        claim = next(c for c in report["claims"] if c["subject"] == row["competitor"] and c["dimension"] == "价格")
        claim["statement"] = claim["value"] = details
    original = deepcopy(report)
    path = export_report(report, "pptx", tmp_path)
    assert report == original
    with ZipFile(path) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide7.xml"))
        visible = [node.text or "" for node in slide.iter() if node.tag.endswith("}t")]
        for row in report["comparison"]:
            assert all(line in visible for line in row["price"].splitlines())
        notes = _text(archive, "ppt/notesSlides/notesSlide10.xml")
        manifest = json.loads(re.search(r"BRIEFFORGE_SNAPSHOT_BEGIN\s*(.*?)\s*BRIEFFORGE_SNAPSHOT_END", notes, re.DOTALL).group(1))
        assert [claim["statement"] for claim in manifest["claims"] if claim["dimension"] == "价格"] == [row["price"] for row in report["comparison"]]


def test_different_currency_or_unconfirmed_price_never_shares_price_axis():
    report = sample_report()
    assert build_export_model(report)["chart"]["kind"] == "price"
    report["comparison"][1]["price"] = "团队版 AUD 27/席位/月，按年付费，未含税。"
    assert build_export_model(report)["chart"]["kind"] == "coverage"
    report = sample_report()
    next(c for c in report["claims"] if c["dimension"] == "价格")["status"] = "uncertain"
    assert build_export_model(report)["chart"]["kind"] == "coverage"


def test_unsupported_format_and_path_component_sanitized(tmp_path):
    report = sample_report()
    with pytest.raises(ValueError):
        export_report(report, "html", tmp_path)
    report["id"] = "../../escape"
    path = export_report(report, "docx", tmp_path)
    assert path.parent == tmp_path
    assert not list(tmp_path.glob(".export-*"))


if __name__ == "__main__":
    import sys

    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/export-sample")
    output.mkdir(parents=True, exist_ok=True)
    sample = sample_report()
    (output / "snapshot.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    for format in ("docx", "pptx"):
        print(export_report(sample, format, output))
