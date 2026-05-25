from pathlib import Path

import pytest

from bsllmner_viewer.etl.load_sra_accessions import (
    REQUIRED_COLUMNS,
    iter_sra_accessions,
)

_HEADER = "\t".join(
    [
        "Accession", "Submission", "Status", "Updated", "Published", "Received",
        "Type", "Center", "Visibility", "Alias", "Experiment", "Sample", "Study",
        "Loaded", "Spots", "Bases", "Md5sum", "BioSample", "BioProject", "ReplacedBy",
    ]
)


def _row(
    accession: str,
    type_: str,
    *,
    status: str = "live",
    biosample: str = "-",
    bioproject: str = "-",
    sample: str = "-",
    study: str = "-",
    experiment: str = "-",
) -> str:
    cells = [
        accession, "SRA001000", status, "2020-01-01", "2020-01-01", "2020-01-01",
        type_, "GEO", "public", "-", experiment, sample, study,
        "-", "-", "-", "-", biosample, bioproject, "-",
    ]
    return "\t".join(cells)


def _write_tsv(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "SRA_Accessions_test.tab"
    p.write_text(_HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_iter_yields_all_types(tmp_path: Path) -> None:
    src = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="SAMN1", bioproject="PRJNA1",
                 sample="SRS1", study="SRP1", experiment="SRX1"),
            _row("SRR1", "RUN", biosample="SAMN1", bioproject="PRJNA1"),
            _row("SRS1", "SAMPLE", biosample="SAMN1", bioproject="PRJNA1"),
        ],
    )
    rows = list(iter_sra_accessions(src))
    assert len(rows) == 3
    srx = next(r for r in rows if r.type == "EXPERIMENT")
    assert srx.accession == "SRX1"
    assert srx.biosample == "SAMN1"
    assert srx.bioproject == "PRJNA1"
    assert srx.sample == "SRS1"
    assert srx.study == "SRP1"
    assert srx.experiment == "SRX1"
    assert srx.status == "live"


def test_dash_is_normalized_to_none(tmp_path: Path) -> None:
    src = _write_tsv(tmp_path, [_row("SRX1", "EXPERIMENT")])
    rows = list(iter_sra_accessions(src))
    assert len(rows) == 1
    row = rows[0]
    assert row.biosample is None
    assert row.bioproject is None
    assert row.sample is None
    assert row.study is None
    assert row.experiment is None


def test_status_is_preserved(tmp_path: Path) -> None:
    src = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", status="live"),
            _row("SRX2", "EXPERIMENT", status="suppressed"),
            _row("SRX3", "EXPERIMENT", status="withdrawn"),
        ],
    )
    rows = list(iter_sra_accessions(src))
    statuses = sorted(r.status for r in rows)
    assert statuses == ["live", "suppressed", "withdrawn"]


def test_missing_required_column_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tab"
    bad.write_text("Accession\tType\tFoo\nSRX1\tEXPERIMENT\tbar\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        list(iter_sra_accessions(bad))


def test_empty_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.tab"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty TSV"):
        list(iter_sra_accessions(empty))


def test_required_columns_cover_build_inputs() -> None:
    needed = {
        "Accession", "Type", "Status",
        "Experiment", "Sample", "Study",
        "BioSample", "BioProject",
    }
    assert needed.issubset(set(REQUIRED_COLUMNS))
