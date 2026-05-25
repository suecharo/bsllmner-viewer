from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bsllmner_viewer.etl.build_srx_links import build_srx_links

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
) -> str:
    cells = [
        accession, "SRA001000", status, "2020-01-01", "2020-01-01", "2020-01-01",
        type_, "GEO", "public", "-", accession, sample, study,
        "-", "-", "-", "-", biosample, bioproject, "-",
    ]
    return "\t".join(cells)


def _write_tsv(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "SRA_Accessions_test.tab"
    p.write_text(_HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def _write_samples(tmp_path: Path, accessions: list[str]) -> Path:
    table = pa.table({"accession": accessions})
    p = tmp_path / "samples.parquet"
    pq.write_table(table, p)
    return p


def _read_rows(path: Path) -> list[dict[str, object]]:
    return pq.read_table(path).to_pylist()


def test_filters_by_biosample_and_type(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="SAMN1", bioproject="PRJNA1",
                 sample="SRS1", study="SRP1"),
            _row("SRX2", "EXPERIMENT", biosample="SAMN_unknown",
                 bioproject="PRJNA2"),
            _row("SRR1", "RUN", biosample="SAMN1"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    rows = _read_rows(out)
    assert len(rows) == 1
    assert rows[0]["srx"] == "SRX1"
    assert rows[0]["accession"] == "SAMN1"
    assert rows[0]["bioproject"] == "PRJNA1"
    assert rows[0]["sra_sample"] == "SRS1"
    assert rows[0]["sra_study"] == "SRP1"
    assert rows[0]["status"] == "live"


def test_one_biosample_to_many_srx(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="SAMN1"),
            _row("SRX2", "EXPERIMENT", biosample="SAMN1"),
            _row("SRX3", "EXPERIMENT", biosample="SAMN1"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    rows = _read_rows(out)
    assert sorted(str(r["srx"]) for r in rows) == ["SRX1", "SRX2", "SRX3"]
    assert all(r["accession"] == "SAMN1" for r in rows)


def test_status_filter_disabled(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="SAMN1", status="live"),
            _row("SRX2", "EXPERIMENT", biosample="SAMN1", status="suppressed"),
            _row("SRX3", "EXPERIMENT", biosample="SAMN1", status="withdrawn"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    rows = _read_rows(out)
    statuses = sorted(str(r["status"]) for r in rows)
    assert statuses == ["live", "suppressed", "withdrawn"]


def test_non_experiment_types_excluded(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRR1", "RUN", biosample="SAMN1"),
            _row("SRS1", "SAMPLE", biosample="SAMN1"),
            _row("SRP1", "STUDY", biosample="SAMN1"),
            _row("SRA1", "SUBMISSION"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    assert _read_rows(out) == []


def test_duplicate_srx_kept_first(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="SAMN1", status="live"),
            _row("SRX1", "EXPERIMENT", biosample="SAMN1", status="suppressed"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    rows = _read_rows(out)
    assert len(rows) == 1
    assert rows[0]["status"] == "live"


def test_missing_source_raises(tmp_path: Path) -> None:
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    with pytest.raises(FileNotFoundError):
        build_srx_links(tmp_path / "no_such.tab", samples, out)


def test_biosample_dash_excluded(tmp_path: Path) -> None:
    tsv = _write_tsv(
        tmp_path,
        [
            _row("SRX1", "EXPERIMENT", biosample="-"),
            _row("SRX2", "EXPERIMENT", biosample="SAMN1"),
        ],
    )
    samples = _write_samples(tmp_path, ["SAMN1"])
    out = tmp_path / "srx_links.parquet"
    build_srx_links(tsv, samples, out)
    rows = _read_rows(out)
    assert len(rows) == 1
    assert rows[0]["srx"] == "SRX2"
