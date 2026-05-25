"""NCBI `SRA_Accessions.tab` の streaming reader。

事前 download 済みの cache (`scripts/fetch_sra_accessions.py` 参照) を 1 行ずつ読み、
必要 column を `SraAccessionsRow` として yield する。32 GB の TSV を memory に
全部展開しないよう csv.DictReader で streaming する。
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS: tuple[str, ...] = (
    "Accession",
    "Type",
    "Status",
    "Experiment",
    "Sample",
    "Study",
    "BioSample",
    "BioProject",
)


@dataclass(frozen=True, slots=True)
class SraAccessionsRow:
    accession: str
    type: str
    status: str
    experiment: str | None
    sample: str | None
    study: str | None
    biosample: str | None
    bioproject: str | None


def _normalize(value: str | None) -> str | None:
    """NCBI の `-` (= no value) と空白 / 空文字列を None に正規化する。"""
    if value is None:
        return None
    s = value.strip()
    if not s or s == "-":
        return None
    return s


def iter_sra_accessions(path: Path) -> Iterator[SraAccessionsRow]:
    # csv.field_size_limit は SRA_Accessions の長大 cell (まれに Alias 等が長い) 対策。
    csv.field_size_limit(sys.maxsize)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty TSV (no header row)")
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"{path}: missing required columns {sorted(missing)} "
                f"(header={reader.fieldnames})"
            )
        for row in reader:
            accession = _normalize(row.get("Accession"))
            type_ = _normalize(row.get("Type"))
            status = _normalize(row.get("Status"))
            if accession is None or type_ is None or status is None:
                continue
            yield SraAccessionsRow(
                accession=accession,
                type=type_,
                status=status,
                experiment=_normalize(row.get("Experiment")),
                sample=_normalize(row.get("Sample")),
                study=_normalize(row.get("Study")),
                biosample=_normalize(row.get("BioSample")),
                bioproject=_normalize(row.get("BioProject")),
            )
