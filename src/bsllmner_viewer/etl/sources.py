import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bsllmner_viewer.etl.types import SourceSystemId


class SourceSystem(BaseModel):
    """1 系統 (chip-atlas-hg38 / chip-atlas-mm10 / rnaseq-human) の定義。

    docs/etl.md「入力 source」テーブルの行に対応。
    """

    model_config = ConfigDict(frozen=True)

    id: SourceSystemId
    organism: str
    in_chip_atlas: bool
    chip_atlas_genome: str | None
    input_glob: str
    result_glob: str
    input_is_wrapped: bool
    """True なら input JSONL の各行が {"BioSample": {...}} でラップされている。"""


SOURCE_SYSTEMS: tuple[SourceSystem, ...] = (
    SourceSystem(
        id="chip-atlas-hg38",
        organism="Homo sapiens",
        in_chip_atlas=True,
        chip_atlas_genome="hg38",
        input_glob="chip-atlas-hg38/input/bs_entries_hg38.jsonl",
        result_glob="chip-atlas-hg38/result/select_*.json",
        input_is_wrapped=False,
    ),
    SourceSystem(
        id="chip-atlas-mm10",
        organism="Mus musculus",
        in_chip_atlas=True,
        chip_atlas_genome="mm10",
        input_glob="chip-atlas-mm10/input/bs_entries_mm10.jsonl",
        result_glob="chip-atlas-mm10/result/select_*.json",
        input_is_wrapped=False,
    ),
    SourceSystem(
        id="rnaseq-human",
        organism="Homo sapiens",
        in_chip_atlas=False,
        chip_atlas_genome=None,
        input_glob="rnaseq-human/input/bs_entries_*.jsonl",
        result_glob="rnaseq-human/result/select_rnaseq_*.json",
        input_is_wrapped=True,
    ),
)


def get_source_system(source_id: SourceSystemId) -> SourceSystem:
    for s in SOURCE_SYSTEMS:
        if s.id == source_id:
            return s
    raise ValueError(f"unknown source_system: {source_id}")


def iter_result_files(data_dir: Path, source: SourceSystem) -> Iterator[Path]:
    yield from sorted(data_dir.glob(source.result_glob))


def iter_input_files(data_dir: Path, source: SourceSystem) -> Iterator[Path]:
    yield from sorted(data_dir.glob(source.input_glob))


_RNASEQ_RESULT_YM = re.compile(r"(\d{4})-(\d{2})(?:_retry)?\.json$")
_RNASEQ_INPUT_YM = re.compile(r"(\d{4})-(\d{2})-\d{2}")


def iter_run_pairs(
    data_dir: Path, source: SourceSystem
) -> Iterator[tuple[list[Path], Path]]:
    """`(対応する input file たち, 1 件の result file)` のペアを yield する。

    - chip-atlas (hg38 / mm10): input 1 / result 1 なので、すべての result に同じ input を紐付ける。
    - rnaseq-human: filename の `YYYY-MM` で input と result をマッチングする。
    """
    results = list(iter_result_files(data_dir, source))
    inputs = list(iter_input_files(data_dir, source))
    if source.id != "rnaseq-human":
        for r in results:
            yield (inputs, r)
        return

    input_by_ym: dict[str, list[Path]] = {}
    for inp in inputs:
        m = _RNASEQ_INPUT_YM.search(inp.name)
        if m:
            input_by_ym.setdefault(f"{m.group(1)}-{m.group(2)}", []).append(inp)
    for r in results:
        m = _RNASEQ_RESULT_YM.search(r.name)
        key = f"{m.group(1)}-{m.group(2)}" if m else ""

        yield (input_by_ym.get(key, []), r)
