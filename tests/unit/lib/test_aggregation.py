from __future__ import annotations

from pathlib import Path

import pytest

from bsllmner_viewer.lib.aggregation import (
    SampleFilters,
    bubble_dataset,
    can_roll_up,
    cohort_count,
    cohort_samples,
    cumulative_bubble_dataset,
    gap_heatmap_pivot,
    term_sample_count,
    top_terms,
)
from bsllmner_viewer.lib.duckdb import get_conn


def test_top_terms_ranks_by_distinct_sample_count(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    pairs = top_terms(con, "disease", SampleFilters(), top_n=10)
    # MONDO:1 has 2 samples (A1, A2); others one each. The ranking head must
    # be MONDO:1; the tail order is implementation-defined.
    assert pairs[0] == ("MONDO:1", "neoplasm")
    assert {term_id for term_id, _ in pairs} == {
        "MONDO:1",
        "MONDO:2",
        "MONDO:10",
        "MONDO:11",
    }


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


def test_cohort_facts_cells_unions_distinct_cells(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # Two cells with disjoint sample sets: A1 (MONDO:1 + CHEBI:1) and
    # A2 (MONDO:1 + CHEBI:2). Union ⇒ {A1, A2}.
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_cells=[
            [("disease", "MONDO:1"), ("drug", "CHEBI:1")],
            [("disease", "MONDO:1"), ("drug", "CHEBI:2")],
        ],
    )
    assert sorted(df["accession"].tolist()) == ["A1", "A2"]
    assert cohort_count(
        con,
        SampleFilters(),
        facts_cells=[
            [("disease", "MONDO:1"), ("drug", "CHEBI:1")],
            [("disease", "MONDO:1"), ("drug", "CHEBI:2")],
        ],
    ) == 2


def test_cohort_facts_cells_ands_within_a_cell(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # Single cell requiring both disease=MONDO:1 AND drug=CHEBI:1 ⇒ only A1.
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_cells=[[("disease", "MONDO:1"), ("drug", "CHEBI:1")]],
    )
    assert df["accession"].tolist() == ["A1"]


def test_cohort_facts_cells_intersects_with_facts_terms(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # facts_cells union = {A1 (MONDO:1+CHEBI:1), A3 (MONDO:2)}.
    # facts_terms forces drug=CHEBI:1 too, which only A1 satisfies.
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_terms=[("drug", "CHEBI:1")],
        facts_cells=[
            [("disease", "MONDO:1")],
            [("disease", "MONDO:2")],
        ],
    )
    assert df["accession"].tolist() == ["A1"]


def test_cohort_facts_cells_skips_empty_inner_cells(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # Empty inner cells must not collapse the OR into an empty parenthesis
    # (which would be invalid SQL) and must not implicitly match everything.
    df = cohort_samples(
        con,
        SampleFilters(),
        facts_cells=[[], [("disease", "MONDO:2")], []],
    )
    assert df["accession"].tolist() == ["A3"]


def test_cohort_facts_cells_all_empty_behaves_like_none(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # If every inner cell is empty there is no constraint to apply, so the
    # caller gets the full sample set (matching `facts_cells=None`).
    n_filtered = cohort_count(
        con, SampleFilters(), facts_cells=[[], []]
    )
    n_unfiltered = cohort_count(con, SampleFilters())
    assert n_filtered == n_unfiltered


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
        (2024, "MONDO:10", "Homo sapiens"): (1, 1),
        (2024, "MONDO:11", "Homo sapiens"): (1, 1),
        (2025, "MONDO:2", "Mus musculus"): (1, 0),
    }


# ---- depth roll-up ----


def test_can_roll_up_for_supported_fields() -> None:
    # Fields with a real ontology hierarchy roll up.
    assert can_roll_up("disease") is True
    assert can_roll_up("tissue") is True
    assert can_roll_up("cell_type") is True
    assert can_roll_up("drug") is True
    # Cellosaurus is self-loop only by design; NCBIGene is not in ontology.parquet.
    assert can_roll_up("cell_line") is False
    assert can_roll_up("knockout_gene") is False


def test_top_terms_rolls_leaves_to_depth_zero(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    pairs = top_terms(con, "disease", SampleFilters(), top_n=10, roll_up_depth=0)
    # MONDO:1 absorbs A1, A2 (leaf MONDO:1), A4 (MONDO:10), A5 (MONDO:11) = 4.
    # MONDO:2 stays at its self-loop (already depth=0) with A3 = 1.
    assert pairs[0] == ("MONDO:1", "neoplasm")
    assert dict(pairs) == {"MONDO:1": "neoplasm", "MONDO:2": "diabetes"}


def test_gap_heatmap_pivot_rolls_x_axis_up(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = gap_heatmap_pivot(
        con,
        "disease",
        "drug",
        SampleFilters(),
        x_roll_up_depth=0,
    )
    cells = {
        (r["x_term_id"], r["y_term_id"]): int(r["sample_count"])
        for _, r in df.iterrows()
    }
    # A4, A5 carry no `drug` facts, so the heatmap inner join drops them.
    # A1 (MONDO:1 ↔ CHEBI:1) and A2 (MONDO:1 ↔ CHEBI:2) remain — but each is
    # rolled into MONDO:1, so we still see two distinct cells.
    assert cells == {
        ("MONDO:1", "CHEBI:1"): 1,
        ("MONDO:1", "CHEBI:2"): 1,
    }


def test_gap_heatmap_pivot_roll_up_uses_ontology_label(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = gap_heatmap_pivot(
        con,
        "disease",
        "drug",
        SampleFilters(),
        x_roll_up_depth=0,
    )
    labels = {r["x_term_id"]: r["x_label"] for _, r in df.iterrows()}
    # Rolled label comes from ontology.parquet, not from the leaf facts.label.
    assert labels["MONDO:1"] == "neoplasm"


def test_roll_up_ignored_for_non_hierarchical_fields(
    aggregation_parquet_dir: Path,
) -> None:
    # cell_line maps to Cellosaurus (self-loop only) and roll-up is silently
    # disabled — passing a depth must behave the same as depth=None.
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    no_roll = top_terms(con, "cell_line", SampleFilters(), top_n=10)
    rolled = top_terms(
        con, "cell_line", SampleFilters(), top_n=10, roll_up_depth=0
    )
    assert no_roll == rolled


# ---- cumulative_bubble_dataset ----


def test_cumulative_bubble_dataset_fills_year_gaps_and_cumsums(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cumulative_bubble_dataset(con, "disease", SampleFilters(), top_n=10)

    # year range present in data: 2024 (A1/A2/A4/A5) and 2025 (A3).
    assert sorted(df["submission_year"].astype(int).unique().tolist()) == [
        2024,
        2025,
    ]

    rows = {
        (
            int(r["submission_year"]),
            r["term_id"],
            r["organism_normalized"],
        ): (int(r["sample_count_cum"]), int(r["chip_atlas_count_cum"]))
        for _, r in df.iterrows()
    }
    # MONDO:1 (Homo): A1+A2 in 2024 → carries to 2025
    # MONDO:2 (Mus):  no 2024 row at all → 0; A3 in 2025 → 1
    # MONDO:10 (Homo): A4 in 2024 → carries to 2025
    # MONDO:11 (Homo): A5 in 2024 → carries to 2025
    assert rows == {
        (2024, "MONDO:1", "Homo sapiens"): (2, 2),
        (2025, "MONDO:1", "Homo sapiens"): (2, 2),
        (2024, "MONDO:2", "Mus musculus"): (0, 0),
        (2025, "MONDO:2", "Mus musculus"): (1, 0),
        (2024, "MONDO:10", "Homo sapiens"): (1, 1),
        (2025, "MONDO:10", "Homo sapiens"): (1, 1),
        (2024, "MONDO:11", "Homo sapiens"): (1, 1),
        (2025, "MONDO:11", "Homo sapiens"): (1, 1),
    }


def test_cumulative_bubble_dataset_empty_when_no_data(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cumulative_bubble_dataset(
        con,
        "disease",
        SampleFilters(organism_normalized=["NonExistent"]),
        top_n=10,
    )
    assert df.empty
    assert "sample_count_cum" in df.columns
    assert "chip_atlas_count_cum" in df.columns


# ---- term_sample_count ----


def test_term_sample_count_counts_distinct_samples(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # MONDO:1 leaf has A1 + A2; both are in_chip_atlas.
    assert term_sample_count(
        con, "disease", "MONDO:1", SampleFilters()
    ) == (2, 2)
    # MONDO:2 has only A3 (Mus, not in chip-atlas).
    assert term_sample_count(
        con, "disease", "MONDO:2", SampleFilters()
    ) == (1, 0)


def test_term_sample_count_respects_filters(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    n_sample, n_chip = term_sample_count(
        con,
        "disease",
        "MONDO:1",
        SampleFilters(organism_normalized=["Mus musculus"]),
    )
    # Mus musculus filter drops A1/A2 (Homo) → 0 hits for MONDO:1.
    assert (n_sample, n_chip) == (0, 0)


def test_term_sample_count_unknown_term_returns_zero(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    assert term_sample_count(
        con, "disease", "MONDO:99999", SampleFilters()
    ) == (0, 0)


def test_term_sample_count_rejects_unknown_field(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown field"):
        term_sample_count(con, "title", "MONDO:1", SampleFilters())
