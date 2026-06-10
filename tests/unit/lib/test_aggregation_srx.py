"""SRX-related aggregation tests.

samples.parquet carries ``srx_first`` / ``srx_count`` inline (filled by
``build-srx-links``'s enrich step), so ``cohort_samples`` returns those two
scalar columns without an extra query. Per-SRX drill-down passes the cohort
accession list to ``cohort_srx_links`` which joins ``srx_links`` to
``samples``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bsllmner_viewer.lib.aggregation import (
    SampleFilters,
    cohort_samples,
    cohort_srx_links,
)
from bsllmner_viewer.lib.duckdb import get_conn


def _by_accession(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {row["accession"]: row for _, row in df.iterrows()}


def test_cohort_samples_inline_srx_first_and_count(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    by_acc = _by_accession(cohort_samples(con, SampleFilters()))
    # A1 has 2 SRX (SRX1, SRX2); first SRX is SRX1, count = 2.
    assert by_acc["A1"]["srx"] == "SRX1"
    assert int(by_acc["A1"]["srx_count"]) == 2
    # A2 has 1 SRX; A5 has 3 SRX (SRX5 / SRX6 / SRX7 → first is SRX5).
    assert by_acc["A2"]["srx"] == "SRX3"
    assert int(by_acc["A2"]["srx_count"]) == 1
    assert by_acc["A5"]["srx"] == "SRX5"
    assert int(by_acc["A5"]["srx_count"]) == 3
    # A4 has no SRX → first SRX is NULL (pandas NaN) and count = 0.
    assert pd.isna(by_acc["A4"]["srx"])
    assert int(by_acc["A4"]["srx_count"]) == 0


def test_cohort_samples_srx_columns_survive_facts_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_samples(
        con, SampleFilters(), facts_terms=[("disease", "MONDO:1")]
    )
    by_acc = _by_accession(df)
    assert set(by_acc) == {"A1", "A2"}
    assert by_acc["A1"]["srx"] == "SRX1"
    assert int(by_acc["A1"]["srx_count"]) == 2
    assert int(by_acc["A2"]["srx_count"]) == 1


def test_cohort_srx_links_expands_to_one_row_per_srx(
    aggregation_parquet_dir: Path,
) -> None:
    # All 5 BS, but A4 has no SRX → no JOIN row. Remaining rows:
    # A1(2) + A2(1) + A3(1) + A5(3) = 7.
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_srx_links(con, ["A1", "A2", "A3", "A4", "A5"])
    assert len(df) == 7
    assert sorted(df["srx"].tolist()) == [
        "SRX1", "SRX2", "SRX3", "SRX4", "SRX5", "SRX6", "SRX7",
    ]
    assert sorted(df.loc[df["accession"] == "A1", "srx"].tolist()) == [
        "SRX1", "SRX2",
    ]
    assert "A4" not in df["accession"].tolist()


def test_cohort_srx_links_carries_source_system(
    aggregation_parquet_dir: Path,
) -> None:
    # A3 は source_system="rnaseq-human" (ChIP-Atlas 系統ではない)。UI 側は
    # この値を ``lib/chip_atlas.bigwig_url`` / ``peak_bed_url`` に渡し、
    # ChIP-Atlas 系統に該当しないときに deep link を出さないかを判定する。
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_srx_links(con, ["A3"])
    assert df["srx"].tolist() == ["SRX4"]
    assert df["source_system"].tolist() == ["rnaseq-human"]


def test_cohort_srx_links_limit_caps_srx_rows(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_srx_links(
        con, ["A1", "A2", "A3", "A4", "A5"], limit=3
    )
    assert len(df) == 3


def test_cohort_srx_links_empty_accessions_returns_empty(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_srx_links(con, [])
    assert df.empty
    assert list(df.columns) == [
        "accession",
        "srx",
        "bioproject",
        "sra_study",
        "sra_sample",
        "status",
        "source_system",
    ]
