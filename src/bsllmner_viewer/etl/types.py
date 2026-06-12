from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LlmTimingFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_duration: int
    load_duration: int
    eval_count: int
    eval_duration: int
    prompt_eval_count: int


class ExtractEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    accession: str
    extracted: dict[str, Any] | None
    raw_output: str | None
    llm_timing: LlmTimingFields | None = None


class ResolvedValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str
    term_id: str | None = None
    term_uri: str | None = None
    label: str | None = None
    exact_match: bool | None = None
    reasoning: str | None = None


class SelectEntry(BaseModel):
    """SelectResult.entries[] の 1 件。

    search_results / text2term_results / select_timings は PoC では facts/runs に載せないため
    Pydantic から field を消して読み飛ばす（extra='ignore'）。memory 節約のため。
    """

    model_config = ConfigDict(extra="ignore")

    extract: ExtractEntry
    results: dict[str, list[ResolvedValue]] = Field(default_factory=dict)


RunStatus = Literal["running", "completed", "failed", "interrupted"]


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_name: str
    model: str
    thinking: bool = False
    start_time: datetime
    end_time: datetime | None = None
    status: RunStatus
    processing_time_sec: float | None = None
    total_entries: int | None = None


SourceSystemId = Literal[
    "chip-atlas-hg38", "chip-atlas-mm10", "rnaseq-human", "rnaseq-mouse"
]


class BsInputEntry(BaseModel):
    """input JSONL の chip-atlas / rnaseq の差を吸収した normalized 形。"""

    model_config = ConfigDict(extra="ignore")

    accession: str
    publication_date: datetime | None = None
    organism: str | None = None
    title: str | None = None
    bioproject: str | None = None
