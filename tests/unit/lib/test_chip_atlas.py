from bsllmner_viewer.lib.chip_atlas import (
    SOURCE_SYSTEM_TO_GENOME,
    bigwig_url,
    genome_of,
    is_chip_atlas_source,
    peak_bed_url,
)


def test_is_chip_atlas_source_recognises_hg38_and_mm10() -> None:
    assert is_chip_atlas_source("chip-atlas-hg38") is True
    assert is_chip_atlas_source("chip-atlas-mm10") is True


def test_is_chip_atlas_source_rejects_rnaseq_and_unknown() -> None:
    assert is_chip_atlas_source("rnaseq-human") is False
    assert is_chip_atlas_source("") is False
    assert is_chip_atlas_source(None) is False
    assert is_chip_atlas_source("brand-new-source") is False


def test_genome_of_for_each_known_source() -> None:
    assert genome_of("chip-atlas-hg38") == "hg38"
    assert genome_of("chip-atlas-mm10") == "mm10"
    assert genome_of("rnaseq-human") is None
    assert genome_of("brand-new-source") is None
    assert genome_of(None) is None


def test_bigwig_url_only_for_chip_atlas() -> None:
    assert bigwig_url("chip-atlas-hg38", "SRX123") == (
        "https://chip-atlas.dbcls.jp/data/hg38/eachData/bw/SRX123.bw"
    )
    assert bigwig_url("chip-atlas-mm10", "DRX9") == (
        "https://chip-atlas.dbcls.jp/data/mm10/eachData/bw/DRX9.bw"
    )
    assert bigwig_url("rnaseq-human", "SRX1") is None
    assert bigwig_url(None, "SRX1") is None


def test_peak_bed_url_only_for_chip_atlas() -> None:
    assert peak_bed_url("chip-atlas-hg38", "SRX123") == (
        "https://chip-atlas.dbcls.jp/data/hg38/eachData/bed05/SRX123.05.bed"
    )
    assert peak_bed_url("chip-atlas-mm10", "DRX9") == (
        "https://chip-atlas.dbcls.jp/data/mm10/eachData/bed05/DRX9.05.bed"
    )
    assert peak_bed_url("rnaseq-human", "SRX1") is None


def test_source_system_to_genome_invariant() -> None:
    # SOURCE_SYSTEM_TO_GENOME is the single source of truth — keep it tight.
    assert set(SOURCE_SYSTEM_TO_GENOME.keys()) == {
        "chip-atlas-hg38",
        "chip-atlas-mm10",
        "rnaseq-human",
    }
    assert SOURCE_SYSTEM_TO_GENOME["chip-atlas-hg38"] == "hg38"
    assert SOURCE_SYSTEM_TO_GENOME["chip-atlas-mm10"] == "mm10"
    assert SOURCE_SYSTEM_TO_GENOME["rnaseq-human"] is None
