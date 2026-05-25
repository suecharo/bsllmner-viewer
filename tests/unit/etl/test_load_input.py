from pathlib import Path

from bsllmner_viewer.etl.load_input import iter_bs_entries
from bsllmner_viewer.etl.sources import get_source_system


def test_chipatlas_normalize_flat(fixture_dir: Path) -> None:
    source = get_source_system("chip-atlas-hg38")
    entries = list(iter_bs_entries(fixture_dir / "bs_entries_chipatlas_minimal.jsonl", source))
    assert len(entries) == 2
    e1, e2 = entries
    assert e1.accession == "SAMN00000001"
    assert e1.organism == "Homo sapiens"
    assert e1.publication_date is not None
    assert e1.publication_date.year == 2013
    assert e1.title == "HeLa ChIP-seq"
    assert e1.bioproject == "PRJDB1234"
    assert e2.accession == "SAMN00000002"
    assert e2.bioproject == "PRJDB9999"  # Attribute が dict (単体) でも拾える


def test_rnaseq_normalize_wrapped(fixture_dir: Path) -> None:
    source = get_source_system("rnaseq-human")
    entries = list(iter_bs_entries(fixture_dir / "bs_entries_rnaseq_minimal.jsonl", source))
    assert len(entries) == 1
    e = entries[0]
    assert e.accession == "SAMN00000003"
    assert e.organism == "Homo sapiens"
    assert e.publication_date is not None
    assert e.publication_date.year == 2025
    assert e.title == "RNA-seq sample"
    assert e.bioproject == "PRJDB5678"


def test_chipatlas_and_rnaseq_use_same_schema(fixture_dir: Path) -> None:
    chip = list(
        iter_bs_entries(
            fixture_dir / "bs_entries_chipatlas_minimal.jsonl",
            get_source_system("chip-atlas-hg38"),
        )
    )
    rna = list(
        iter_bs_entries(
            fixture_dir / "bs_entries_rnaseq_minimal.jsonl",
            get_source_system("rnaseq-human"),
        )
    )
    assert all(set(e.model_dump().keys()) == set(rna[0].model_dump().keys()) for e in chip)
