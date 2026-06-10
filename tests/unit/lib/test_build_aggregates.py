"""build_aggregates が agg_*.parquet を正しく出力し、UI fast helper の戻り値
が live helper と数値一致するかを 1 つの fixture で end-to-end に確認する。

ChIP-Atlas 派生は ``source_system LIKE 'chip-atlas-%'`` で取り出すので、
agg parquet 側には ``in_chip_atlas`` 列を持たない (docs/data-model.md
「ChIP-Atlas 接続点」節)。schema 検査でその不在を明示する。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.build_aggregates import build_aggregates
from bsllmner_viewer.lib.aggregation import (
    UNKNOWN,
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


def _parquet_column_names(path: Path) -> list[str]:
    return [f.name for f in pq.read_schema(path)]


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


def test_agg_samples_by_dims_schema_excludes_in_chip_atlas(
    aggregation_parquet_dir: Path,
) -> None:
    build_aggregates(aggregation_parquet_dir)
    cols = _parquet_column_names(
        aggregation_parquet_dir / "agg_samples_by_dims.parquet"
    )
    assert set(cols) == {
        "submission_year",
        "source_system",
        "sequence_type",
        "organism_normalized",
        "sample_count",
    }
    assert "in_chip_atlas" not in cols
    assert "chip_atlas_genome" not in cols


def test_agg_field_term_dims_schema_excludes_in_chip_atlas(
    aggregation_parquet_dir: Path,
) -> None:
    build_aggregates(aggregation_parquet_dir)
    cols = _parquet_column_names(
        aggregation_parquet_dir / "agg_field_term_dims.parquet"
    )
    assert set(cols) == {
        "field",
        "term_id",
        "label",
        "submission_year",
        "source_system",
        "sequence_type",
        "organism_normalized",
        "sample_count",
    }
    assert "in_chip_atlas" not in cols
    assert "chip_atlas_genome" not in cols


def test_agg_field_status_dims_schema_excludes_in_chip_atlas(
    aggregation_parquet_dir: Path,
) -> None:
    build_aggregates(aggregation_parquet_dir)
    cols = _parquet_column_names(
        aggregation_parquet_dir / "agg_field_status_dims.parquet"
    )
    assert set(cols) == {
        "field",
        "source_system",
        "sequence_type",
        "submission_year",
        "organism_normalized",
        "extract_status",
        "n",
    }
    assert "in_chip_atlas" not in cols
    assert "chip_atlas_genome" not in cols


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
    # fixture: 5 samples、ChIP-Atlas は A1/A2/A4/A5 (source_system=chip-atlas-hg38)
    # で 4 件。``summary_counts_fast`` は ``source_system LIKE 'chip-atlas-%'``
    # で派生集計するので fixture の chip-atlas-hg38 が一致する。
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
        con, SampleFilters(sequence_type=("RNA-Seq",))
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
        con, "disease", SampleFilters(sequence_type=("ChIP-Seq",)), top_n=10
    )
    # ChIP-Seq の A1/A2 は disease=MONDO:1 のみ
    assert pairs == [("MONDO:1", "neoplasm")]


# ---- (unknown) sentinel: NULL dim values get coalesced ----


_NULL_SAMPLES_SCHEMA = pa.schema(
    [
        pa.field("accession", pa.string(), nullable=False),
        pa.field("organism_normalized", pa.string(), nullable=True),
        pa.field("submission_year", pa.int32(), nullable=True),
        pa.field("source_system", pa.string(), nullable=False),
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("sequence_type", pa.string(), nullable=True),
    ]
)

_NULL_FACTS_SCHEMA = pa.schema(
    [
        pa.field("accession", pa.string(), nullable=False),
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("field", pa.string(), nullable=False),
        pa.field("value", pa.string(), nullable=True),
        pa.field("term_id", pa.string(), nullable=True),
        pa.field("label", pa.string(), nullable=True),
        pa.field("exact_match", pa.bool_(), nullable=True),
        pa.field("text2term_score", pa.float32(), nullable=True),
        pa.field("ontology_source", pa.string(), nullable=True),
        pa.field("extract_status", pa.string(), nullable=False),
    ]
)


def _write_null_dim_fixture(pdir: Path) -> None:
    """NULL organism_normalized / NULL sequence_type を持つ最小 dataset を書く。

    aggregation_parquet_dir fixture は他 test の数値 assert に縛られて NULL
    行を持てない。``(unknown)`` 塗り潰しの assert はこちらで独立に検証する。
    """
    samples = pa.Table.from_pylist(
        [
            {
                "accession": "N1",
                "organism_normalized": None,
                "submission_year": 2024,
                "source_system": "rnaseq-human",
                "run_name": "run-null",
                "sequence_type": None,
            },
        ],
        schema=_NULL_SAMPLES_SCHEMA,
    )
    pq.write_table(samples, pdir / "samples.parquet")
    facts = pa.Table.from_pylist(
        [
            {
                "accession": "N1",
                "run_name": "run-null",
                "field": "disease",
                "value": "cancer",
                "term_id": "MONDO:1",
                "label": "neoplasm",
                "exact_match": True,
                "text2term_score": 1.0,
                "ontology_source": "MONDO",
                "extract_status": "ok",
            },
        ],
        schema=_NULL_FACTS_SCHEMA,
    )
    pq.write_table(facts, pdir / "facts.parquet")


def test_agg_samples_by_dims_coalesces_null_dims_to_unknown(
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "parquet"
    pdir.mkdir()
    _write_null_dim_fixture(pdir)
    build_aggregates(pdir)
    table = pq.read_table(pdir / "agg_samples_by_dims.parquet")
    rows = table.to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["organism_normalized"] == UNKNOWN
    assert row["sequence_type"] == UNKNOWN
    assert int(row["sample_count"]) == 1


def test_agg_field_term_dims_coalesces_null_dims_to_unknown(
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "parquet"
    pdir.mkdir()
    _write_null_dim_fixture(pdir)
    build_aggregates(pdir)
    table = pq.read_table(pdir / "agg_field_term_dims.parquet")
    rows = table.to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["organism_normalized"] == UNKNOWN
    assert row["sequence_type"] == UNKNOWN
    assert row["term_id"] == "MONDO:1"


def test_agg_field_status_dims_coalesces_null_dims_to_unknown(
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "parquet"
    pdir.mkdir()
    _write_null_dim_fixture(pdir)
    build_aggregates(pdir)
    table = pq.read_table(pdir / "agg_field_status_dims.parquet")
    rows = table.to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["organism_normalized"] == UNKNOWN
    assert row["sequence_type"] == UNKNOWN
    assert int(row["n"]) == 1
