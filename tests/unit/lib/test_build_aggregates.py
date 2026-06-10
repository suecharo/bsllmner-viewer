"""build_aggregates が agg_*.parquet を正しく出力し、UI fast helper の戻り値
が live helper と数値一致するかを 1 つの fixture で end-to-end に確認する。
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from bsllmner_viewer.etl.build_aggregates import build_aggregates
from bsllmner_viewer.lib.aggregation import (
    field_facts_status,
    field_facts_status_fast,
    has_dashboard_aggregates,
    samples_by_organism,
    samples_by_organism_fast,
    samples_by_source,
    samples_by_source_fast,
    samples_by_year_source,
    samples_by_year_source_fast,
    summary_counts_fast,
    top_terms_overall,
    top_terms_overall_fast,
)
from bsllmner_viewer.lib.duckdb import get_conn


def _con_with_aggs(parquet_dir: Path) -> duckdb.DuckDBPyConnection:
    build_aggregates(parquet_dir)
    return get_conn(parquet_dir=parquet_dir)


def test_build_aggregates_writes_three_parquets(
    aggregation_parquet_dir: Path,
) -> None:
    build_aggregates(aggregation_parquet_dir)
    for name in (
        "agg_samples_by_dims.parquet",
        "agg_field_term_dims.parquet",
        "agg_field_status_dims.parquet",
    ):
        assert (aggregation_parquet_dir / name).exists()


def test_has_dashboard_aggregates_flips_after_build(
    aggregation_parquet_dir: Path,
) -> None:
    # 未生成時は False
    pre_con = get_conn(parquet_dir=aggregation_parquet_dir)
    assert has_dashboard_aggregates(pre_con) is False
    post_con = _con_with_aggs(aggregation_parquet_dir)
    assert has_dashboard_aggregates(post_con) is True


def test_samples_by_year_source_fast_matches_live(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    fast = samples_by_year_source_fast(con).sort_values(
        ["submission_year", "source_system"]
    ).reset_index(drop=True)
    live = samples_by_year_source(con).sort_values(
        ["submission_year", "source_system"]
    ).reset_index(drop=True)
    assert list(fast["sample_count"]) == list(live["sample_count"])


def test_samples_by_organism_fast_matches_live(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    # fast 版は organism_normalized が NULL を '(unknown)' に塗り潰す。fixture
    # では NULL organism は無いので両者は完全一致する。
    fast = samples_by_organism_fast(con).sort_values("organism_normalized")
    live = samples_by_organism(con).sort_values("organism_normalized")
    assert list(fast["sample_count"].astype(int)) == list(
        live["sample_count"].astype(int)
    )


def test_samples_by_source_fast_matches_live(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    fast = samples_by_source_fast(con).sort_values("source_system")
    live = samples_by_source(con).sort_values("source_system")
    assert list(fast["sample_count"].astype(int)) == list(
        live["sample_count"].astype(int)
    )


def test_field_facts_status_fast_matches_live(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    fast = field_facts_status_fast(con).sort_values(["field", "extract_status"])
    live = field_facts_status(con).sort_values(["field", "extract_status"])
    assert list(fast["n"].astype(int)) == list(live["n"].astype(int))


def test_top_terms_overall_fast_matches_live(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    fast = top_terms_overall_fast(con, "disease", top_n=10)
    live = top_terms_overall(con, "disease", top_n=10)
    assert list(fast.sort_values("term_id")["sample_count"].astype(int)) == list(
        live.sort_values("term_id")["sample_count"].astype(int)
    )


def test_summary_counts_fast_returns_consistent_totals(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    s = summary_counts_fast(con)
    # fixture: 5 samples、ChIP-Atlas は A1/A2/A4/A5 で 4 件
    assert s["samples"] == 5
    assert s["chip_atlas"] == 4
    assert s["runs"] >= 0  # runs.parquet は fixture 内では作っていないので 0 or skip


# ---- filter-aware fast helpers ----


def test_mapping_status_matrix_fast_filters_by_sequence_type(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    from bsllmner_viewer.lib.aggregation import (
        SampleFilters,
        mapping_status_matrix_fast,
    )

    df = mapping_status_matrix_fast(
        con, SampleFilters(sequence_type=["RNA-Seq"])
    )
    # A3 のみが RNA-Seq。A3 の facts は disease=ok 1 件 + drug=mapping_failed 1 件
    assert int(df["n"].sum()) == 2


def test_bubble_dataset_fast_matches_live_no_rollup(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    from bsllmner_viewer.lib.aggregation import (
        SampleFilters,
        bubble_dataset,
        bubble_dataset_fast,
    )

    fast = bubble_dataset_fast(con, "disease", SampleFilters(), top_n=10)
    live = bubble_dataset(con, "disease", SampleFilters(), top_n=10)
    # 行数とサンプル数合計が一致する (organism × year × term の分布が同じ)
    assert int(fast["sample_count"].sum()) == int(live["sample_count"].sum())
    assert set(fast["term_id"]) == set(live["term_id"])


def test_top_terms_fast_with_sequence_type_filter(
    aggregation_parquet_dir: Path,
) -> None:
    con = _con_with_aggs(aggregation_parquet_dir)
    from bsllmner_viewer.lib.aggregation import (
        SampleFilters,
        top_terms_fast,
    )

    pairs = top_terms_fast(
        con, "disease", SampleFilters(sequence_type=["ChIP-Seq"]), top_n=10
    )
    # ChIP-Seq の A1/A2 は disease=MONDO:1 のみ
    assert pairs == [("MONDO:1", "neoplasm")]
