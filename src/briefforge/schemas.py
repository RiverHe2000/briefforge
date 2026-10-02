from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(Input):
    title: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=5, max_length=6000)
    audience: str = Field(default="产品与管理团队", min_length=1, max_length=200)
    time_range: str = Field(default="最近12个月", min_length=1, max_length=200)
    web_enabled: bool = True
    competitors: list[str] = Field(min_length=1, max_length=4)
    dimensions: list[str] = Field(
        default_factory=lambda: ["目标客户", "核心功能", "价格与套餐", "近期变化", "差异化机会"]
    )
    mode: Literal["synthetic", "public"] = "public"

    @field_validator("competitors", "dimensions")
    @classmethod
    def valid_names(cls, values: list[str]) -> list[str]:
        values = [v.strip() for v in values]
        if any(not v or len(v) > 120 for v in values) or len(set(values)) != len(values):
            raise ValueError("名称不能为空、重复或超过120字")
        if len(values) > 12:
            raise ValueError("最多12个比较维度")
        return values


class ProjectPatch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    question: str | None = Field(default=None, min_length=5, max_length=6000)
    audience: str | None = Field(default=None, min_length=1, max_length=200)
    time_range: str | None = Field(default=None, min_length=1, max_length=200)
    web_enabled: bool | None = None
    competitors: list[str] | None = None
    dimensions: list[str] | None = None


class SourceCreate(Input):
    title: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=500_000)
    competitor: str = Field(default="", max_length=120)
    kind: str = Field(default="document", max_length=60)
    logical_key: str | None = Field(default=None, max_length=200)
    published_at: str | None = Field(default=None, max_length=60)
    url: str | None = Field(default=None, max_length=2048)


class RunCreate(Input):
    mode: Literal["replay", "live"] = "replay"
    model_profile: Literal["qwen-default", "gemini-budget"] = "gemini-budget"
    architecture: Literal["multi", "single", "pipeline"] = "multi"
    budget_usd: float | None = Field(default=None, gt=0, le=0.30)
    instructions: str = Field(default="", max_length=6000)
    bucket: Literal["development", "evaluation", "web", "reserve"] = "development"


class Revision(Input):
    instructions: str = Field(min_length=3, max_length=6000)


class ExportCreate(Input):
    format: Literal["docx", "pptx"]


class DemoCreate(Input):
    scenario: str = Field(default="default", max_length=80)


class UrlImport(Input):
    url: str = Field(min_length=10, max_length=2048)
    competitor: str = Field(default="", max_length=120)
