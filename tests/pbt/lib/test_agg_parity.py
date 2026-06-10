"""Property-based parity test for ``_filter_clauses`` (live) vs ``_agg_filter_clauses`` (agg).

The fast path (``mapping_status_matrix_fast`` / ``mapping_status_over_time_fast``
/ ``top_terms_fast`` / ``bubble_dataset_fast``) reads ``agg_*.parquet`` while
the live path reads ``samples`` JOIN ``facts``. Both must return the same
sample count for any ``SampleFilters`` value — GEN-8 keeps that invariant
under random filter combinations so the QUA-1 / QUA-3 ``UNKNOWN`` sentinel
expansion stays correct as the codebase evolves.

The shared ``aggregation_parquet_dir`` fixture in ``tests/unit/lib/conftest.py``
provides the deterministic samples + facts dataset; we run ``build-aggregates``
inside the test to materialise the matching ``agg_*.parquet`` and load both
parquet groups into one in-memory DuckDB connection.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from bsllmner_viewer.etl.build_aggregates import build_aggregates
from bsllmner_viewer.lib.aggregation import (
    UNKNOWN,
    SampleFilters,
    bubble_dataset,
    bubble_dataset_fast,
    has_dashboard_aggregates,
    mapping_status_matrix,
    mapping_status_matrix_fast,
    mapping_status_over_time,
    mapping_status_over_time_fast,
    top_terms,
    top_terms_fast,
)

_FIXTURE_ORGANISMS: tuple[str, ...] = ("Homo sapiens", "Mus musculus", UNKNOWN)
_FIXTURE_SOURCES: tuple[str, ...] = ("chip-atlas-hg38", "rnaseq-human")
_FIXTURE_SEQ_TYPES: tuple[str, ...] = ("ChIP-Seq", "ATAC-Seq", "RNA-Seq", UNKNOWN)
_FIXTURE_YEAR_MIN: int = 2024
_FIXTURE_YEAR_MAX: int = 2025

# Fields the fast path supports (no roll-up). Cellosaurus / NCBIGene-style
# fields don't have agg coverage so we don't test parity for them here.
_FIELDS_WITH_AGG: tuple[str, ...] = ("disease", "drug")


@pytest.fixture()
def parity_conn(aggregation_parquet_dir: Path) -> duckdb.DuckDBPyConnection:
    """A single DuckDB connection with live + agg parquet views."""
    build_aggregates(aggregation_parquet_dir)
    con = duckdb.connect(database=":memory:")
    for name in (
        "samples",
        "facts",
        "ontology",
        "srx_links",
        "agg_samples_by_dims",
        "agg_field_term_dims",
        "agg_field_status_dims",
    ):
        path = aggregation_parquet_dir / f"{name}.parquet"
        if path.exists():
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
            )
    assert has_dashboard_aggregates(con)
    return con


def _filters_strategy() -> st.SearchStrategy[SampleFilters]:
    """Random SampleFilters drawn from the fixture's value set.

    The strategy is intentionally narrow: every dim value is something the
    fixture actually contains (or the ``UNKNOWN`` sentinel) so the invariant
    is exercised across the full Cartesian product without burning rounds on
    filters that obviously yield zero rows.
    """
    year_range = st.tuples(
        st.integers(min_value=_FIXTURE_YEAR_MIN, max_value=_FIXTURE_YEAR_MAX),
        st.integers(min_value=_FIXTURE_YEAR_MIN, max_value=_FIXTURE_YEAR_MAX),
    ).map(lambda yr: (min(yr), max(yr)))
    return st.builds(
        lambda org, src, seq, ymin_max, use_year: SampleFilters(
            organism_normalized=tuple(org),
            source_system=tuple(src),
            sequence_type=tuple(seq),
            submission_year_min=ymin_max[0] if use_year else None,
            submission_year_max=ymin_max[1] if use_year else None,
        ),
        st.lists(st.sampled_from(_FIXTURE_ORGANISMS), unique=True, max_size=3),
        st.lists(st.sampled_from(_FIXTURE_SOURCES), unique=True, max_size=2),
        st.lists(st.sampled_from(_FIXTURE_SEQ_TYPES), unique=True, max_size=4),
        year_range,
        st.booleans(),
    )


def _normalise_for_compare(df: object, key_cols: tuple[str, ...]) -> dict[tuple[object, ...], int]:
    """Reduce a DataFrame to ``(key) -> total count`` for set-equality checks.

    The fast path stores ``UNKNOWN`` for NULL dim values while the live path
    drops NULL rows when the filter is not the empty set (covered by
    ``_in_clause_with_unknown``), so the totals along the surviving keys must
    match exactly between the two paths.
    """
    import pandas as pd

    assert isinstance(df, pd.DataFrame)
    if df.empty:
        return {}
    sum_col = "n" if "n" in df.columns else "sample_count"
    grouped = df.groupby(list(key_cols), dropna=False)[sum_col].sum()
    return {k if isinstance(k, tuple) else (k,): int(v) for k, v in grouped.items()}


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(filters=_filters_strategy())
def test_mapping_status_matrix_fast_matches_live(
    parity_conn: duckdb.DuckDBPyConnection, filters: SampleFilters
) -> None:
    live = mapping_status_matrix(parity_conn, filters)
    fast = mapping_status_matrix_fast(parity_conn, filters)
    keys = ("field", "source_system", "extract_status")
    assert _normalise_for_compare(live, keys) == _normalise_for_compare(fast, keys)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(filters=_filters_strategy())
def test_mapping_status_over_time_fast_matches_live(
    parity_conn: duckdb.DuckDBPyConnection, filters: SampleFilters
) -> None:
    live = mapping_status_over_time(parity_conn, filters)
    fast = mapping_status_over_time_fast(parity_conn, filters)
    keys = ("field", "submission_year", "extract_status")
    assert _normalise_for_compare(live, keys) == _normalise_for_compare(fast, keys)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(filters=_filters_strategy(), field=st.sampled_from(_FIELDS_WITH_AGG))
def test_top_terms_fast_matches_live_set(
    parity_conn: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    field: str,
) -> None:
    """Both paths return the same TopN term set under the filter.

    Order can differ slightly for ties — we compare the term set, not the
    ordering, and verify that the live ranking surfaces a superset (the fast
    path is limited to the top-200 agg snapshot).
    """
    top_n = 50
    live = top_terms(parity_conn, field, filters, top_n)
    fast = top_terms_fast(parity_conn, field, filters, top_n)
    live_terms = {t for t, _ in live}
    fast_terms = {t for t, _ in fast}
    # Fast is built from a top-200 agg subset, so it must be a subset of live.
    assert fast_terms.issubset(live_terms)
    # On the tiny fixture (≤ 50 distinct terms) the two must coincide.
    assert fast_terms == live_terms


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(filters=_filters_strategy(), field=st.sampled_from(_FIELDS_WITH_AGG))
def test_bubble_dataset_fast_matches_live_sum(
    parity_conn: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    field: str,
) -> None:
    """The (year, term) sample-count totals match across paths.

    organism_normalized is part of the bubble row but the live path can split
    one BioSample across multiple organism rows only when the fixture has
    multi-organism data — it doesn't. So per-term sums collapse 1:1.
    """
    live = bubble_dataset(parity_conn, field, filters, top_n=50)
    fast = bubble_dataset_fast(parity_conn, field, filters, top_n=50)
    keys = ("submission_year", "term_id")
    assert _normalise_for_compare(live, keys) == _normalise_for_compare(fast, keys)
