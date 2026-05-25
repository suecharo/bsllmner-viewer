from pathlib import Path

from bsllmner_viewer.etl.load_select import (
    iter_select_entries,
    read_error_count,
    read_run_metadata,
)


def test_iter_select_entries_yields_all(fixture_dir: Path) -> None:
    entries = list(iter_select_entries(fixture_dir / "select_minimal.json"))
    assert [e.extract.accession for e in entries] == [
        "SAMN00000001",
        "SAMN00000002",
        "SAMN00000003",
    ]
    # entry1: cell_line resolved
    assert len(entries[0].results.get("cell_line", [])) == 1
    assert entries[0].results["cell_line"][0].term_id == "CVCL:0030"
    # entry2: extract failure (extracted is null)
    assert entries[1].extract.extracted is None
    # entry3: drug array
    assert entries[2].extract.extracted is not None
    assert entries[2].extract.extracted["drug"] == ["unknown_drug_1", "unknown_drug_2"]


def test_read_run_metadata(fixture_dir: Path) -> None:
    metadata = read_run_metadata(fixture_dir / "select_minimal.json")
    assert metadata.run_name == "test_run_v1"
    assert metadata.model == "test-model"
    assert metadata.status == "completed"
    assert metadata.total_entries == 3


def test_read_error_count_zero(fixture_dir: Path) -> None:
    assert read_error_count(fixture_dir / "select_minimal.json") == 0
