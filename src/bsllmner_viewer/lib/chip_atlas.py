"""ChIP-Atlas との接続点を 1 module に集約する純関数 helper。

samples.parquet には `in_chip_atlas` / `chip_atlas_genome` 列を持たない。両者は
`source_system` の prefix で 1:1 に決まるため、本 module の dict / 関数を経由して
派生情報を取り出す。仕様は docs/data-model.md の「ChIP-Atlas 接続点」節と
docs/ui.md の `lib/chip_atlas.py` API 表を SSOT とする。
"""

from __future__ import annotations

from typing import Final

SOURCE_SYSTEM_TO_GENOME: Final[dict[str, str | None]] = {
    "chip-atlas-hg38": "hg38",
    "chip-atlas-mm10": "mm10",
    "rnaseq-human": None,
}


def is_chip_atlas_source(source_system: str | None) -> bool:
    if not source_system:
        return False
    return source_system.startswith("chip-atlas-")


def genome_of(source_system: str | None) -> str | None:
    if not source_system:
        return None
    return SOURCE_SYSTEM_TO_GENOME.get(source_system)


def bigwig_url(source_system: str | None, srx: str) -> str | None:
    genome = genome_of(source_system)
    if genome is None:
        return None
    return f"https://chip-atlas.dbcls.jp/data/{genome}/eachData/bw/{srx}.bw"


def peak_bed_url(source_system: str | None, srx: str) -> str | None:
    genome = genome_of(source_system)
    if genome is None:
        return None
    return f"https://chip-atlas.dbcls.jp/data/{genome}/eachData/bed05/{srx}.05.bed"
