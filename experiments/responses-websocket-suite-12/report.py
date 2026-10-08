"""Shared, locally validated pilot report. No API client."""

from pydantic import BaseModel, ConfigDict


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    value: int | float | str | bool | None
    evidence_ids: list[str]
    derivation: str


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    evidence_ids: list[str]
    explanation: str


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str
    conclusion: str
    facts: list[Fact]
    findings: list[Finding]
    recommendation: str
    limitations: list[str]
