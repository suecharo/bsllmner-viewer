from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from bsllmner_viewer.lib.aggregation import (
    UNKNOWN,
    SampleFilters,
    bubble_dataset,
    can_roll_up,
    cohort_breakdown,
    cohort_count,
    cohort_facts_columns,
    cohort_samples,
    cumulative_bubble_dataset,
    field_facts_status,
    gap_heatmap_pivot,
    mapping_status_matrix,
    mapping_status_over_time,
    samples_by_organism,
    samples_by_source,
    samples_by_year_source,
    term_sample_count,
    top_terms,
    top_terms_overall,
    top_unmapped_values,
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
        SampleFilters(organism_normalized=("Mus musculus",)),
        top_n=10,
    )
    assert pairs == [("MONDO:2", "diabetes")]


def test_top_terms_respects_source_system_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # 「ChIP-Atlas 系統に該当しない BS」は source_system="rnaseq-human" のみ。
    pairs = top_terms(
        con,
        "disease",
        SampleFilters(source_system=("rnaseq-human",)),
        top_n=10,
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
            r["secondary_count"],
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
        SampleFilters(organism_normalized=("NonExistent",)),
    )
    assert df.empty
    assert list(df.columns) == [
        "x_term_id",
        "x_label",
        "y_term_id",
        "y_label",
        "sample_count",
        "secondary_count",
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
            int(r["secondary_count"]),
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
        ): (int(r["sample_count_cum"]), int(r["secondary_count_cum"]))
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
        SampleFilters(organism_normalized=("NonExistent",)),
        top_n=10,
    )
    assert df.empty
    assert "sample_count_cum" in df.columns
    assert "secondary_count_cum" in df.columns


# ---- term_sample_count ----


def test_term_sample_count_counts_distinct_samples(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # MONDO:1 leaf has A1 + A2; both sit in chip-atlas-hg38 → overlay = 2.
    assert term_sample_count(
        con, "disease", "MONDO:1", SampleFilters()
    ) == (2, 2)
    # MONDO:2 has only A3 (rnaseq-human, not a ChIP-Atlas source).
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
        SampleFilters(organism_normalized=("Mus musculus",)),
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


# ---- cohort_breakdown ----


def test_cohort_breakdown_partitions_by_year_organism_source(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_breakdown(con, SampleFilters())
    rows = {
        (
            int(r["submission_year"]),
            r["organism_normalized"],
            r["source_system"],
        ): int(r["sample_count"])
        for _, r in df.iterrows()
    }
    # A1/A2/A4/A5 -> (2024, Homo, chip-atlas-hg38) = 4 samples
    # A3 -> (2025, Mus, rnaseq-human) = 1 sample
    assert rows == {
        (2024, "Homo sapiens", "chip-atlas-hg38"): 4,
        (2025, "Mus musculus", "rnaseq-human"): 1,
    }


def test_cohort_breakdown_respects_facts_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_breakdown(
        con, SampleFilters(), facts_terms=[("disease", "MONDO:1")]
    )
    # facts_terms=disease:MONDO:1 -> only A1, A2
    assert int(df["sample_count"].sum()) == 2


# ---- Home dashboard aggregates ----


def test_samples_by_year_source(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = samples_by_year_source(con)
    rows = {
        (int(r["submission_year"]), r["source_system"]): int(r["sample_count"])
        for _, r in df.iterrows()
    }
    assert rows == {(2024, "chip-atlas-hg38"): 4, (2025, "rnaseq-human"): 1}


def test_samples_by_organism(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = samples_by_organism(con)
    rows = {r["organism_normalized"]: int(r["sample_count"]) for _, r in df.iterrows()}
    assert rows == {"Homo sapiens": 4, "Mus musculus": 1}


def test_samples_by_source(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = samples_by_source(con)
    rows = {r["source_system"]: int(r["sample_count"]) for _, r in df.iterrows()}
    assert rows == {"chip-atlas-hg38": 4, "rnaseq-human": 1}


def test_field_facts_status(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = field_facts_status(con)
    rows = {(r["field"], r["extract_status"]): int(r["n"]) for _, r in df.iterrows()}
    # disease: 5 ok (A1, A2, A3, A4, A5)
    # drug: 2 ok (A1, A2), 1 mapping_failed (A3)
    # tissue: 2 mapping_failed (A1, A4), 1 extract_failed (A2)
    assert rows == {
        ("disease", "ok"): 5,
        ("drug", "ok"): 2,
        ("drug", "mapping_failed"): 1,
        ("tissue", "mapping_failed"): 2,
        ("tissue", "extract_failed"): 1,
    }


def test_top_terms_overall_returns_sample_counts(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = top_terms_overall(con, "disease", top_n=10)
    rows = {
        r["term_id"]: (r["label"], int(r["sample_count"]))
        for _, r in df.iterrows()
    }
    assert rows == {
        "MONDO:1": ("neoplasm", 2),
        "MONDO:2": ("diabetes", 1),
        "MONDO:10": ("breast neoplasm", 1),
        "MONDO:11": ("lung neoplasm", 1),
    }
    # MONDO:1 must be ranked first (descending by sample_count).
    assert df.iloc[0]["term_id"] == "MONDO:1"


def test_top_terms_overall_rejects_unknown_field(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown field"):
        top_terms_overall(con, "title", top_n=10)


# ---- Curation aggregates ----


def test_mapping_status_matrix_groups_by_field_source_status(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = mapping_status_matrix(con, SampleFilters())
    rows = {
        (r["field"], r["source_system"], r["extract_status"]): int(r["n"])
        for _, r in df.iterrows()
    }
    assert rows == {
        ("disease", "chip-atlas-hg38", "ok"): 4,
        ("disease", "rnaseq-human", "ok"): 1,
        ("drug", "chip-atlas-hg38", "ok"): 2,
        ("drug", "rnaseq-human", "mapping_failed"): 1,
        ("tissue", "chip-atlas-hg38", "extract_failed"): 1,
        ("tissue", "chip-atlas-hg38", "mapping_failed"): 2,
    }


def test_mapping_status_matrix_respects_filters(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = mapping_status_matrix(
        con, SampleFilters(source_system=("rnaseq-human",))
    )
    rows = {
        (r["field"], r["source_system"], r["extract_status"]): int(r["n"])
        for _, r in df.iterrows()
    }
    assert rows == {
        ("disease", "rnaseq-human", "ok"): 1,
        ("drug", "rnaseq-human", "mapping_failed"): 1,
    }


def test_mapping_status_over_time_groups_by_field_year_status(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = mapping_status_over_time(con, SampleFilters())
    rows = {
        (r["field"], int(r["submission_year"]), r["extract_status"]): int(r["n"])
        for _, r in df.iterrows()
    }
    assert rows == {
        ("disease", 2024, "ok"): 4,
        ("disease", 2025, "ok"): 1,
        ("drug", 2024, "ok"): 2,
        ("drug", 2025, "mapping_failed"): 1,
        ("tissue", 2024, "extract_failed"): 1,
        ("tissue", 2024, "mapping_failed"): 2,
    }


def test_top_unmapped_values_collapses_duplicates(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = top_unmapped_values(con, "tissue", top_n=10, filters=SampleFilters())
    # "weird tissue X" appears for A1 and A4 -> n=2, sample_count=2.
    rows = {
        r["value"]: (int(r["n"]), int(r["sample_count"]))
        for _, r in df.iterrows()
    }
    assert rows == {"weird tissue X": (2, 2)}


def test_top_unmapped_values_excludes_extract_failed(
    aggregation_parquet_dir: Path,
) -> None:
    # A2 has tissue extract_failed with value=NULL -> filtered out by the
    # NOT NULL value clause, even though it's also "unmapped" (term_id NULL).
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = top_unmapped_values(con, "tissue", top_n=10, filters=SampleFilters())
    assert (df["value"] == "weird tissue X").all()


def test_top_unmapped_values_respects_filters(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # rnaseq-human のみに絞ると A1/A4 (chip-atlas-hg38) は弾かれるので
    # tissue mapping_failed は 0 件になる。
    df = top_unmapped_values(
        con,
        "tissue",
        top_n=10,
        filters=SampleFilters(source_system=("rnaseq-human",)),
    )
    assert df.empty


def test_top_unmapped_values_rejects_unknown_field(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown field"):
        top_unmapped_values(con, "title", top_n=10, filters=SampleFilters())


# ---- bubble_dataset roll-up ----


def test_bubble_dataset_rolls_leaves_to_depth_zero(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = bubble_dataset(con, "disease", SampleFilters(), roll_up_depth=0)
    rows = {
        (
            int(r["submission_year"]),
            r["term_id"],
            r["organism_normalized"],
        ): int(r["sample_count"])
        for _, r in df.iterrows()
    }
    # MONDO:1 absorbs A1+A2 (already at MONDO:1), A4 (MONDO:10), A5 (MONDO:11)
    # — all Homo sapiens, all 2024 → 4 samples in one cell.
    # MONDO:2 (depth=0 already) keeps A3 alone.
    assert rows == {
        (2024, "MONDO:1", "Homo sapiens"): 4,
        (2025, "MONDO:2", "Mus musculus"): 1,
    }


def test_cumulative_bubble_dataset_propagates_roll_up(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cumulative_bubble_dataset(
        con, "disease", SampleFilters(), top_n=10, roll_up_depth=0
    )
    # After roll-up: 2 (term, organism) groups, each reindexed across the full
    # 2024-2025 year range.
    rows = {
        (
            int(r["submission_year"]),
            r["term_id"],
            r["organism_normalized"],
        ): int(r["sample_count_cum"])
        for _, r in df.iterrows()
    }
    assert rows == {
        (2024, "MONDO:1", "Homo sapiens"): 4,
        (2025, "MONDO:1", "Homo sapiens"): 4,
        (2024, "MONDO:2", "Mus musculus"): 0,
        (2025, "MONDO:2", "Mus musculus"): 1,
    }


# ---- cohort_facts_columns ----


def _facts_only_con(
    rows: list[tuple[str, str, str, str | None, str | None]],
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE facts ("
        "  accession VARCHAR, run_name VARCHAR, field VARCHAR, "
        "  term_id VARCHAR, label VARCHAR"
        ")"
    )
    if rows:
        con.executemany(
            "INSERT INTO facts VALUES (?, ?, ?, ?, ?)", [list(r) for r in rows]
        )
    return con


def test_cohort_facts_columns_empty_accessions_returns_schema_only() -> None:
    df = cohort_facts_columns(duckdb.connect(":memory:"), [], ["disease"])
    assert df.empty
    assert list(df.columns) == ["accession", "field", "value"]


def test_cohort_facts_columns_empty_fields_returns_schema_only() -> None:
    df = cohort_facts_columns(duckdb.connect(":memory:"), ["A1"], [])
    assert df.empty
    assert list(df.columns) == ["accession", "field", "value"]


def test_cohort_facts_columns_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        cohort_facts_columns(duckdb.connect(":memory:"), ["A1"], ["bogus"])


def test_cohort_facts_columns_renders_label_and_term_id(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_facts_columns(con, ["A1"], ["disease", "drug"])
    rows = {(r["accession"], r["field"]): r["value"] for _, r in df.iterrows()}
    assert rows == {
        ("A1", "disease"): "neoplasm (MONDO:1)",
        ("A1", "drug"): "aspirin (CHEBI:1)",
    }


def test_cohort_facts_columns_skips_null_term_id(
    aggregation_parquet_dir: Path,
) -> None:
    # A1 has a tissue row with term_id=NULL (extract_status=mapping_failed).
    # It must NOT appear in the output.
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_facts_columns(con, ["A1"], ["tissue"])
    assert df.empty


def test_cohort_facts_columns_dedupes_and_sorts_by_display() -> None:
    # CHEBI:1 appears twice (same run and again with same label) plus a
    # different run with CHEBI:3. Output must dedupe by term_id and sort
    # by the rendered "label (term_id)" string.
    rows: list[tuple[str, str, str, str | None, str | None]] = [
        ("A1", "run1", "drug", "CHEBI:1", "aspirin"),
        ("A1", "run1", "drug", "CHEBI:1", "aspirin"),
        ("A1", "run1", "drug", "CHEBI:2", "ibuprofen"),
        ("A1", "run2", "drug", "CHEBI:3", "Acetaminophen"),
    ]
    con = _facts_only_con(rows)
    df = cohort_facts_columns(con, ["A1"], ["drug"])
    assert len(df) == 1
    # ASCII order: capital 'A' < lowercase 'a'/'i', so "Acetaminophen ..." sorts first.
    assert df.iloc[0]["value"] == (
        "Acetaminophen (CHEBI:3), aspirin (CHEBI:1), ibuprofen (CHEBI:2)"
    )


def test_cohort_facts_columns_null_label_falls_back_to_term_id() -> None:
    rows: list[tuple[str, str, str, str | None, str | None]] = [
        ("A1", "run1", "disease", "MONDO:99", None),
    ]
    con = _facts_only_con(rows)
    df = cohort_facts_columns(con, ["A1"], ["disease"])
    assert df.iloc[0]["value"] == "MONDO:99"


def test_cohort_facts_columns_collapses_multi_run_same_term_with_diff_labels() -> None:
    # Same term_id, different labels across runs. The output must contain a
    # single segment for MONDO:1, and the chosen label is deterministic
    # (MIN of the rendered display strings — i.e. "cancer (MONDO:1)").
    rows: list[tuple[str, str, str, str | None, str | None]] = [
        ("A1", "run1", "disease", "MONDO:1", "neoplasm"),
        ("A1", "run2", "disease", "MONDO:1", "cancer"),
    ]
    con = _facts_only_con(rows)
    df = cohort_facts_columns(con, ["A1"], ["disease"])
    assert len(df) == 1
    assert df.iloc[0]["value"] == "cancer (MONDO:1)"


def test_cohort_facts_columns_runs_for_unrelated_accessions_are_isolated() -> None:
    rows: list[tuple[str, str, str, str | None, str | None]] = [
        ("A1", "run1", "disease", "MONDO:1", "neoplasm"),
        ("A2", "run1", "disease", "MONDO:2", "diabetes"),
    ]
    con = _facts_only_con(rows)
    df = cohort_facts_columns(con, ["A1"], ["disease"])
    assert list(df["accession"]) == ["A1"]
    assert df.iloc[0]["value"] == "neoplasm (MONDO:1)"


def test_cohort_facts_columns_unrequested_field_excluded() -> None:
    rows: list[tuple[str, str, str, str | None, str | None]] = [
        ("A1", "run1", "disease", "MONDO:1", "neoplasm"),
        ("A1", "run1", "drug", "CHEBI:1", "aspirin"),
    ]
    con = _facts_only_con(rows)
    df = cohort_facts_columns(con, ["A1"], ["disease"])
    assert list(df["field"]) == ["disease"]


# ---- raw_value_term_flow ----


def test_raw_value_term_flow_returns_value_term_pairs(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import raw_value_term_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = raw_value_term_flow(con, "disease", top_n=10, filters=SampleFilters())
    rows = {
        (r["value"], r["term_id"]): (int(r["n"]), int(r["sample_count"]))
        for _, r in df.iterrows()
    }
    # disease facts (extract_status='ok'):
    # cancer→MONDO:1 (A1, A2), diabetes→MONDO:2 (A3),
    # breast cancer→MONDO:10 (A4), lung cancer→MONDO:11 (A5)
    assert rows == {
        ("cancer", "MONDO:1"): (2, 2),
        ("diabetes", "MONDO:2"): (1, 1),
        ("breast cancer", "MONDO:10"): (1, 1),
        ("lung cancer", "MONDO:11"): (1, 1),
    }


def test_raw_value_term_flow_min_count_filters(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import raw_value_term_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = raw_value_term_flow(
        con, "disease", top_n=10, filters=SampleFilters(), min_count=2
    )
    # Only cancer→MONDO:1 has n>=2.
    assert df["value"].tolist() == ["cancer"]
    assert int(df.iloc[0]["n"]) == 2


def test_raw_value_term_flow_excludes_failed_mappings(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import raw_value_term_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = raw_value_term_flow(
        con, "tissue", top_n=10, filters=SampleFilters()
    )
    # tissue rows are all mapping_failed / extract_failed — no ok rows exist,
    # so the Sankey input must be empty.
    assert df.empty


def test_raw_value_term_flow_rejects_unknown_field(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import raw_value_term_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown field"):
        raw_value_term_flow(
            con, "title", top_n=10, filters=SampleFilters()
        )


# ---- momentum_dataset ----


def test_momentum_dataset_computes_delta_and_cumulative(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import momentum_dataset

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = momentum_dataset(con, "disease", SampleFilters(), top_n=10)
    rows = {
        (int(r["submission_year"]), r["term_id"]): (
            int(r["count_abs"]),
            int(r["count_delta"]),
            int(r["count_cum"]),
        )
        for _, r in df.iterrows()
    }
    # MONDO:1 (Homo, 2024) = 2; absent in 2025 → 0
    # MONDO:2 (Mus, 2025) = 1; absent in 2024 → 0
    assert rows[(2024, "MONDO:1")] == (2, 2, 2)
    assert rows[(2025, "MONDO:1")] == (0, -2, 2)
    assert rows[(2024, "MONDO:2")] == (0, 0, 0)
    assert rows[(2025, "MONDO:2")] == (1, 1, 1)


def test_momentum_dataset_empty_when_no_data(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import momentum_dataset

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = momentum_dataset(
        con,
        "disease",
        SampleFilters(organism_normalized=("NonExistent",)),
        top_n=10,
    )
    assert df.empty
    assert list(df.columns) == [
        "term_id",
        "label",
        "submission_year",
        "count_abs",
        "count_delta",
        "count_cum",
    ]


# ---- cumulative_diversity ----


def test_cumulative_diversity_running_union_overall(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cumulative_diversity

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cumulative_diversity(con, "disease", SampleFilters())
    rows = {
        int(r["submission_year"]): (
            int(r["unique_terms"]),
            int(r["cum_unique_terms"]),
        )
        for _, r in df.iterrows()
    }
    # 2024: MONDO:1, MONDO:10, MONDO:11 unique = 3, cum = 3
    # 2025: MONDO:2 new = 1, cum = 4 (running union)
    assert rows == {2024: (3, 3), 2025: (1, 4)}


def test_cumulative_diversity_group_by_organism(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cumulative_diversity

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cumulative_diversity(
        con, "disease", SampleFilters(), group_by="organism_normalized"
    )
    rows = {
        (r["group_value"], int(r["submission_year"])): (
            int(r["unique_terms"]),
            int(r["cum_unique_terms"]),
        )
        for _, r in df.iterrows()
    }
    # Homo's full year range is single year 2024 — diversity 3.
    # Mus's full year range is single year 2025 — diversity 1.
    assert rows == {
        ("Homo sapiens", 2024): (3, 3),
        ("Mus musculus", 2025): (1, 1),
    }


def test_cumulative_diversity_rejects_unknown_group_by(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cumulative_diversity

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    with pytest.raises(ValueError, match="unknown group_by"):
        cumulative_diversity(
            con, "disease", SampleFilters(), group_by="not_a_column"
        )


# ---- concentration_over_time ----


def test_concentration_over_time_gini_and_shannon(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import concentration_over_time

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = concentration_over_time(con, "disease", SampleFilters())
    rows = {
        int(r["submission_year"]): (
            int(r["n_terms"]),
            int(r["total_samples"]),
            float(r["gini"]),
            float(r["shannon"]),
        )
        for _, r in df.iterrows()
    }
    # 2024: terms = {MONDO:1: 2 samples, MONDO:10: 1, MONDO:11: 1} → 4 sample
    # Manual Gini for [1,1,2]: sorted (1,1,2), n=3, total=4
    #   (2*(1*1+2*1+3*2) - (3+1)*4) / (3*4) = (18 - 16) / 12 ≈ 0.1667
    # Shannon normalized: p=[0.25,0.25,0.5]; H/-ln(3)
    n_terms_2024, total_2024, gini_2024, shannon_2024 = rows[2024]
    assert n_terms_2024 == 3
    assert total_2024 == 4
    assert abs(gini_2024 - 1 / 6) < 1e-6
    # 2025: only MONDO:2 with 1 sample — single term ⇒ both metrics are 0
    assert rows[2025] == (1, 1, 0.0, 0.0)
    # Shannon must be > 0 when entropy is non-trivial
    assert 0.0 < shannon_2024 < 1.0


def test_concentration_over_time_empty_when_filtered_out(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import concentration_over_time

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = concentration_over_time(
        con,
        "disease",
        SampleFilters(organism_normalized=("NonExistent",)),
    )
    assert df.empty
    assert list(df.columns) == [
        "submission_year",
        "n_terms",
        "total_samples",
        "gini",
        "shannon",
    ]


# ---- term_hierarchy_breakdown ----


def test_term_hierarchy_breakdown_returns_subtree_with_direct_parent(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import term_hierarchy_breakdown

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = term_hierarchy_breakdown(
        con, "disease", SampleFilters(), max_depth=2
    )
    # Only terms with sample_count > 0 are returned. MONDO:1/10/11/2 all have
    # samples in fixture.
    by_term = {
        r["term_id"]: (
            r["parent_term_id"],
            int(r["depth"]),
            int(r["sample_count"]),
        )
        for _, r in df.iterrows()
    }
    assert by_term["MONDO:1"] == ("", 0, 2)  # root, no parent
    assert by_term["MONDO:2"] == ("", 0, 1)  # root
    assert by_term["MONDO:10"] == ("MONDO:1", 1, 1)  # direct parent MONDO:1
    assert by_term["MONDO:11"] == ("MONDO:1", 1, 1)


def test_term_hierarchy_breakdown_root_term_restricts_subtree(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import term_hierarchy_breakdown

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = term_hierarchy_breakdown(
        con, "disease", SampleFilters(), root_term="MONDO:1", max_depth=2
    )
    # Anchored at MONDO:1 — only MONDO:1 and its descendants (10, 11) appear.
    assert set(df["term_id"]) == {"MONDO:1", "MONDO:10", "MONDO:11"}


def test_term_hierarchy_breakdown_non_hierarchical_field(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import term_hierarchy_breakdown

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = term_hierarchy_breakdown(
        con, "cell_line", SampleFilters(), max_depth=2
    )
    # Cellosaurus has no hierarchy by design — empty result with the right
    # schema.
    assert df.empty
    assert "parent_term_id" in df.columns


def test_term_hierarchy_breakdown_by_year_carries_year_column(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import term_hierarchy_breakdown

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = term_hierarchy_breakdown(
        con, "disease", SampleFilters(), max_depth=2, by_year=True
    )
    assert "submission_year" in df.columns
    # MONDO:1 has 2 samples in 2024 and 0 in 2025 (the latter not surfaced).
    mondo1_rows = df[df["term_id"] == "MONDO:1"]
    assert set(mondo1_rows["submission_year"]) == {2024}


# ---- field_to_field_flow ----


def test_field_to_field_flow_passes_through_gap_pivot(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import field_to_field_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = field_to_field_flow(con, "disease", "drug", SampleFilters())
    rows = {
        (r["x_term_id"], r["y_term_id"]): int(r["sample_count"])
        for _, r in df.iterrows()
    }
    # Same shape as test_gap_heatmap_pivot_disease_x_drug.
    assert rows == {("MONDO:1", "CHEBI:1"): 1, ("MONDO:1", "CHEBI:2"): 1}


def test_field_to_field_flow_empty_when_filtered_out(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import field_to_field_flow

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = field_to_field_flow(
        con,
        "disease",
        "drug",
        SampleFilters(organism_normalized=("NonExistent",)),
    )
    assert df.empty


# ---- cohort_overlap_summary / cohort_term_overlap ----


def test_cohort_overlap_summary_partitions_three_ways() -> None:
    from bsllmner_viewer.lib.aggregation import cohort_overlap_summary

    out = cohort_overlap_summary(["A1", "A2", "A3"], ["A2", "A3", "A4"])
    assert out == {"only_a": 1, "both": 2, "only_b": 1}


def test_cohort_overlap_summary_empty_inputs() -> None:
    from bsllmner_viewer.lib.aggregation import cohort_overlap_summary

    assert cohort_overlap_summary([], []) == {
        "only_a": 0,
        "both": 0,
        "only_b": 0,
    }
    assert cohort_overlap_summary(["A1"], []) == {
        "only_a": 1,
        "both": 0,
        "only_b": 0,
    }


def test_cohort_term_overlap_per_field_jaccard(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cohort_term_overlap

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # A1, A2 share MONDO:1; drugs differ (CHEBI:1 vs CHEBI:2).
    df = cohort_term_overlap(con, ["A1"], ["A2"], fields=["disease", "drug"])
    rows = {r["field"]: r for _, r in df.iterrows()}
    # disease: both have MONDO:1 — Jaccard = 1/1 = 1.0
    assert int(rows["disease"]["n_pinned"]) == 1
    assert int(rows["disease"]["n_current"]) == 1
    assert int(rows["disease"]["n_both"]) == 1
    assert abs(float(rows["disease"]["jaccard"]) - 1.0) < 1e-9
    # drug: disjoint — Jaccard = 0
    assert int(rows["drug"]["n_both"]) == 0
    assert abs(float(rows["drug"]["jaccard"]) - 0.0) < 1e-9


def test_cohort_term_overlap_empty_inputs(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cohort_term_overlap

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_term_overlap(con, [], [], fields=["disease"])
    assert df.empty


def test_cohort_term_overlap_default_fields(
    aggregation_parquet_dir: Path,
) -> None:
    from bsllmner_viewer.lib.aggregation import cohort_term_overlap

    con = get_conn(parquet_dir=aggregation_parquet_dir)
    df = cohort_term_overlap(con, ["A1"], ["A2"])
    # All VALID_FIELDS are surveyed even when most carry no facts.
    from bsllmner_viewer.lib.aggregation import VALID_FIELDS
    assert set(df["field"]) == set(VALID_FIELDS)




# ---- sequence_type filter ----


def test_top_terms_respects_sequence_type_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # A1/A2 are ChIP-Seq → MONDO:1; A4/A5 are ATAC-Seq → MONDO:10/11; A3 is RNA-Seq → MONDO:2.
    pairs = top_terms(
        con,
        "disease",
        SampleFilters(sequence_type=("ATAC-Seq",)),
        top_n=10,
    )
    assert sorted(t for t, _ in pairs) == ["MONDO:10", "MONDO:11"]


def test_cohort_count_with_sequence_type_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    n = cohort_count(con, SampleFilters(sequence_type=("RNA-Seq",)))
    # A3 のみ RNA-Seq
    assert n == 1


def test_filter_clauses_emits_sequence_type_in_list() -> None:
    from bsllmner_viewer.lib.aggregation import _filter_clauses

    clause, params = _filter_clauses(
        SampleFilters(sequence_type=("ChIP-Seq", "ATAC-Seq"))
    )
    assert "s.sequence_type IN (?,?)" in clause
    assert params == ["ChIP-Seq", "ATAC-Seq"]


def test_top_terms_combination_filter(aggregation_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=aggregation_parquet_dir)
    # ChIP-Seq + Homo sapiens で A1/A2 (MONDO:1) のみ。
    pairs = top_terms(
        con,
        "disease",
        SampleFilters(
            sequence_type=("ChIP-Seq",),
            organism_normalized=("Homo sapiens",),
        ),
        top_n=10,
    )
    assert pairs == [("MONDO:1", "neoplasm")]


# ---- (unknown) sentinel ----


def _connect_with_null_samples() -> duckdb.DuckDBPyConnection:
    """In-memory samples + facts with explicit NULL organism / sequence_type.

    UNKNOWN を IN list に含めた filter で NULL 行も拾えるかを検証するため、
    NULL を持つ最小の samples / facts を直接作る。aggregation_parquet_dir の
    fixture には NULL 行が無い (build_samples の正規化により NULL が出ない
    場合が殆ど) ため、UNKNOWN 経路だけは別 fixture を組む。
    """
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE samples ("
        "  accession VARCHAR, organism_normalized VARCHAR, submission_year INT, "
        "  title VARCHAR, source_system VARCHAR, run_name VARCHAR, "
        "  sequence_type VARCHAR, srx_first VARCHAR, srx_count INT"
        ")"
    )
    con.execute(
        "INSERT INTO samples VALUES "
        # B1: organism + sequence_type 共に値あり
        "('B1', 'Homo sapiens', 2024, 't1', 'chip-atlas-hg38', 'run1', "
        "'ChIP-Seq', NULL, 0), "
        # B2: organism NULL, sequence_type 値あり
        "('B2', NULL, 2024, 't2', 'chip-atlas-hg38', 'run1', 'ChIP-Seq', "
        "NULL, 0), "
        # B3: organism 値あり, sequence_type NULL
        "('B3', 'Homo sapiens', 2024, 't3', 'chip-atlas-hg38', 'run1', NULL, "
        "NULL, 0)"
    )
    con.execute(
        "CREATE TABLE facts ("
        "  accession VARCHAR, run_name VARCHAR, field VARCHAR, value VARCHAR, "
        "  term_id VARCHAR, label VARCHAR, exact_match BOOLEAN, "
        "  text2term_score FLOAT, ontology_source VARCHAR, extract_status VARCHAR"
        ")"
    )
    con.execute(
        "INSERT INTO facts VALUES "
        "('B1', 'run1', 'disease', 'c', 'MONDO:1', 'neoplasm', TRUE, 1.0, "
        "'MONDO', 'ok'), "
        "('B2', 'run1', 'disease', 'c', 'MONDO:1', 'neoplasm', TRUE, 1.0, "
        "'MONDO', 'ok'), "
        "('B3', 'run1', 'disease', 'c', 'MONDO:1', 'neoplasm', TRUE, 1.0, "
        "'MONDO', 'ok')"
    )
    return con


def test_unknown_sentinel_in_organism_filter_includes_null_rows() -> None:
    con = _connect_with_null_samples()
    # organism_normalized=(UNKNOWN,) → NULL 行 (B2) のみが残る。
    n = cohort_count(
        con, SampleFilters(organism_normalized=(UNKNOWN,))
    )
    assert n == 1
    # 「Homo sapiens」と UNKNOWN を両方選んだら NULL + 値ありの両方が拾える。
    n_combined = cohort_count(
        con,
        SampleFilters(organism_normalized=("Homo sapiens", UNKNOWN)),
    )
    assert n_combined == 3


def test_unknown_sentinel_in_sequence_type_filter_includes_null_rows() -> None:
    con = _connect_with_null_samples()
    # sequence_type=(UNKNOWN,) → NULL 行 (B3) のみ。
    n = cohort_count(
        con, SampleFilters(sequence_type=(UNKNOWN,))
    )
    assert n == 1
    # ChIP-Seq + UNKNOWN で B1/B2 (ChIP-Seq) + B3 (NULL) の合計 3 件。
    n_combined = cohort_count(
        con, SampleFilters(sequence_type=("ChIP-Seq", UNKNOWN))
    )
    assert n_combined == 3


# ---- overlay axis (gap_heatmap_pivot secondary_count) ----


def test_gap_heatmap_pivot_overlay_axis_sequence_type(
    aggregation_parquet_dir: Path,
) -> None:
    """``overlay_axis="seq:<value>"`` で secondary_count を seq 別に切り替えられる。

    fixture では (MONDO:1, CHEBI:1) は A1 (ChIP-Seq) で構成されるので、
    overlay_axis="seq:ChIP-Seq" のとき secondary_count == 1。
    overlay_axis="seq:ATAC-Seq" だと A1 は ATAC-Seq ではないので 0。
    """
    con = get_conn(parquet_dir=aggregation_parquet_dir)

    df = gap_heatmap_pivot(
        con, "disease", "drug", SampleFilters(), overlay_axis="seq:ChIP-Seq"
    )
    row = df[
        (df["x_term_id"] == "MONDO:1") & (df["y_term_id"] == "CHEBI:1")
    ].iloc[0]
    assert int(row["sample_count"]) == 1
    assert int(row["secondary_count"]) == 1

    df_atac = gap_heatmap_pivot(
        con, "disease", "drug", SampleFilters(), overlay_axis="seq:ATAC-Seq"
    )
    row_atac = df_atac[
        (df_atac["x_term_id"] == "MONDO:1") & (df_atac["y_term_id"] == "CHEBI:1")
    ].iloc[0]
    assert int(row_atac["sample_count"]) == 1
    assert int(row_atac["secondary_count"]) == 0
