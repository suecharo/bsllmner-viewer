import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bsllmner_viewer.etl.types import SourceSystemId


class SourceSystem(BaseModel):
    """1 系統 (chip-atlas-hg38 / chip-atlas-mm10 / rnaseq-human) の定義。

    docs/etl.md「入力 source」テーブルの行に対応。ChIP-Atlas 関連の派生情報
    (in_chip_atlas / genome) はここに持たず、``lib/chip_atlas.py:
    SOURCE_SYSTEM_TO_GENOME`` と ``is_chip_atlas_source(source_system)`` で
    ``id`` から導出する (docs/data-model.md ChIP-Atlas 接続点 節)。

    ``default_sequence_type`` は ``samples.sequence_type`` の fallback。
    chip-atlas-* は per-SRX に ``experimentList.tab`` から正規化した
    sequence_type を入れる前提で None。rnaseq-human は BS dump の選別段で
    既に RNA-Seq に絞っているため ``"RNA-Seq"`` を default にする。
    """

    model_config = ConfigDict(frozen=True)

    id: SourceSystemId
    organism: str
    default_sequence_type: str | None
    input_glob: str
    result_glob: str
    input_is_wrapped: bool
    """True なら input JSONL の各行が {"BioSample": {...}} でラップされている。"""


SOURCE_SYSTEMS: tuple[SourceSystem, ...] = (
    SourceSystem(
        id="chip-atlas-hg38",
        organism="Homo sapiens",
        default_sequence_type=None,
        input_glob="chip-atlas-hg38/input/bs_entries_*.jsonl",
        result_glob="chip-atlas-hg38/result/select_*.json",
        input_is_wrapped=False,
    ),
    SourceSystem(
        id="chip-atlas-mm10",
        organism="Mus musculus",
        default_sequence_type=None,
        input_glob="chip-atlas-mm10/input/bs_entries_*.jsonl",
        result_glob="chip-atlas-mm10/result/select_*.json",
        input_is_wrapped=False,
    ),
    SourceSystem(
        id="rnaseq-human",
        organism="Homo sapiens",
        default_sequence_type="RNA-Seq",
        input_glob="rnaseq-human/input/bs_entries_*.jsonl",
        result_glob="rnaseq-human/result/select_rnaseq_*.json",
        input_is_wrapped=True,
    ),
    SourceSystem(
        id="rnaseq-mouse",
        organism="Mus musculus",
        default_sequence_type="RNA-Seq",
        input_glob="rnaseq-mouse/input/bs_entries_*.jsonl",
        result_glob="rnaseq-mouse/result/select_rnaseq_*.json",
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

    - chip-atlas (hg38 / mm10): input 1〜N / result 1〜M なので、すべての
      result に同じ input list を紐付ける。
    - rnaseq-* (human / mouse): filename の `YYYY-MM` で input と result を
      マッチングする。merged 範囲ファイル
      (例: ``bs_entries_2008-01-01_2013-12-31.jsonl`` ↔
      ``select_rnaseq_mouse_2008-01_2013-12.json``) は regex が両端の
      ``YYYY-MM`` を別々に拾うため厳密マッチしないが、build-samples は
      result 由来の accession を base に samples 行を生成するため致命傷では
      ない (input 由来 column が source_system default に fallback するだけ)。
    """
    results = list(iter_result_files(data_dir, source))
    inputs = list(iter_input_files(data_dir, source))
    if not source.id.startswith("rnaseq-"):
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
