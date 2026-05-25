from __future__ import annotations

from pathlib import Path

import pytest

from bsllmner_viewer.lib.aggregation import (
    SampleFilters,
    bubble_dataset,
    cohort_count,
    cohort_samples,
    gap_heatmap_pivot,
    top_terms,
)
from bsllmner_viewer.lib.duckdb import get_conn


def test_top_terms_ranks_by_distinct_sample_count(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    pairs = top_terms(con, "disease", SampleFilters(), top_n=10)
    # MONDO:1 has 2 samples (A1, A2); MONDO:2 has 1 (A3).
    assert pairs == [("MONDO:1", "neoplasm"), ("MONDO:2", "diabetes")]


def test_top_terms_respects_organism_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    pairs = top_terms(
        con,
        "disease",
        SampleFilters(organism_normalized=["Mus musculus"]),
        top_n=10,
    )
    assert pairs == [("MONDO:2", "diabetes")]


def test_top_terms_respects_in_chip_atlas_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    pairs = top_terms(
        con, "disease", SampleFilters(in_chip_atlas=False), top_n=10
    )
    assert pairs == [("MONDO:2", "diabetes")]


def test_gap_heatmap_pivot_disease_x_drug(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = gap_heatmap_pivot(con, "disease", "drug", SampleFilters())
    # A1: (MONDO:1, CHEBI:1), A2: (MONDO:1, CHEBI:2). A3 has no drug => excluded.
    cells = {
        (r["x_term_id"], r["y_term_id"]): (
            r["sample_count"],
            r["chip_atlas_count"],
        )
        for _, r in df.iterrows()
    }
    assert cells == {
        ("MONDO:1", "CHEBI:1"): (1, 1),
        ("MONDO:1", "CHEBI:2"): (1, 1),
    }


def test_gap_heatmap_pivot_returns_empty_when_no_terms(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = gap_heatmap_pivot(
        con,
        "disease",
        "drug",
        SampleFilters(organism_normalized=["NonExistent"]),
    )
    assert df.empty
    assert list(df.columns) == [
        "x_term_id",
        "x_label",
        "y_term_id",
        "y_label",
        "sample_count",
        "chip_atlas_count",
    ]


def test_gap_heatmap_pivot_rejects_unknown_field(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown field"):
        gap_heatmap_pivot(con, "title", "drug", SampleFilters())


def test_cohort_samples_filters_by_facts_terms(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_terms=[("disease", "MONDO:1")],
    )
    assert sorted(df["accession"].tolist()) == ["A1", "A2"]


def test_cohort_samples_requires_all_facts(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_terms=[("disease", "MONDO:1"), ("drug", "CHEBI:1")],
    )
    assert df["accession"].tolist() == ["A1"]


def test_cohort_count_matches_samples(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    n = cohort_count(
        con, SampleFilters(), facts_terms=[("disease", "MONDO:1")]
    )
    assert n == 2


def test_bubble_dataset_groups_by_year_term_organism(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = bubble_dataset(con, "disease", SampleFilters())
    rows = {
        (int(r["submission_year"]), r["term_id"], r["organism_normalized"]): (
            int(r["sample_count"]),
            int(r["chip_atlas_count"]),
        )
        for _, r in df.iterrows()
    }
    assert rows == {
        (2024, "MONDO:1", "Homo sapiens"): (2, 2),
        (2025, "MONDO:2", "Mus musculus"): (1, 0),
    }
