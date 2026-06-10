"""ChIP-Atlas `experimentList.tab` の streaming reader。

format: tab 区切り、ヘッダ行なし。column 1〜9 が固定で、10 列目以降は
``key=value`` の付加 metadata。本 ETL では sequence_type 算出に必要な
column 1 (SRX) / column 2 (genome_assembly) / column 3 (track_type_class) の
3 つだけ抽出する (~190MB の TSV を全量 in-memory に持たないため)。

cache の取得は ``scripts/fetch_chip_atlas_experiment_list.py`` 経由。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ChipAtlasExperimentRow:
    srx: str
    genome_assembly: str | None
    track_type_class: str | None


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if not s or s.upper() == "NA":
        return None
    return s


def iter_chip_atlas_experiments(path: Path) -> Iterator[ChipAtlasExperimentRow]:
    """1 行 1 SRX を yield。SRX 欠落行は skip する。

    bsllmner-mk2 の ``prepare_chipatlas_bs_entries.py`` と同じ field index 規約
    (0 = srx, 1 = genome_assembly, 2 = track_type_class) に従う。
    """
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) < 3:
                continue
            srx = _normalize(fields[0])
            if srx is None:
                continue
            yield ChipAtlasExperimentRow(
                srx=srx,
                genome_assembly=_normalize(fields[1]) if len(fields) >= 2 else None,
                track_type_class=_normalize(fields[2]) if len(fields) >= 3 else None,
            )
