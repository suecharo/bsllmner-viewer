"""Shared sidebar filter widgets for the Streamlit UI."""

from __future__ import annotations

from typing import cast

import duckdb
import streamlit as st

from bsllmner_viewer.lib.aggregation import SampleFilters


@st.cache_data(show_spinner=False)
def _organism_options(_con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = _con.execute(
        "SELECT DISTINCT organism_normalized FROM samples "
        "WHERE organism_normalized IS NOT NULL "
        "ORDER BY organism_normalized"
    ).fetchall()
    return [r[0] for r in rows]


@st.cache_data(show_spinner=False)
def _source_options(_con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = _con.execute(
        "SELECT DISTINCT source_system FROM samples ORDER BY source_system"
    ).fetchall()
    return [r[0] for r in rows]


_SEQ_TYPE_ORDER: tuple[str, ...] = (
    "ChIP-Seq",
    "ChIP-Seq (input)",
    "ATAC-Seq",
    "DNase-Seq",
    "Bisulfite-Seq",
    "RNA-Seq",
    "Annotation track",
    "mixed",
)


@st.cache_data(show_spinner=False)
def _sequence_type_options(_con: duckdb.DuckDBPyConnection) -> list[str]:
    """Distinct sequence_type 値を、定義済み category を先頭にした並び順で返す。

    sequence_type 列が samples.parquet に無いとき (旧 schema) は空 list を返す
    → UI は multiselect を skip。
    """
    cols = _con.execute("PRAGMA table_info('samples')").fetchall()
    if not any(row[1] == "sequence_type" for row in cols):
        return []
    rows = _con.execute(
        "SELECT DISTINCT sequence_type FROM samples "
        "WHERE sequence_type IS NOT NULL ORDER BY sequence_type"
    ).fetchall()
    distinct = {str(r[0]) for r in rows if r[0] is not None}
    known = [s for s in _SEQ_TYPE_ORDER if s in distinct]
    other = sorted(distinct - set(_SEQ_TYPE_ORDER))
    return [*known, *other]


@st.cache_data(show_spinner=False)
def _year_bounds(_con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    row = _con.execute(
        "SELECT MIN(submission_year), MAX(submission_year) FROM samples "
        "WHERE submission_year IS NOT NULL"
    ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        return (2000, 2026)
    return (int(row[0]), int(row[1]))


def sidebar_filters(con: duckdb.DuckDBPyConnection) -> SampleFilters:
    organisms = _organism_options(con)
    sources = _source_options(con)
    seq_types = _sequence_type_options(con)
    year_min, year_max = _year_bounds(con)

    st.sidebar.header("Filters")
    # When a widget has a `key`, st.session_state is the source of truth — passing
    # both `default=` (or `value=`) and a populated key triggers Streamlit's
    # session_state-vs-widget-default warning. Seed defaults via session_state
    # ahead of widget instantiation (see Cohort page) instead.
    if "filter_organism" not in st.session_state:
        st.session_state["filter_organism"] = []
    if "filter_source" not in st.session_state:
        st.session_state["filter_source"] = []
    if "filter_sequence_type" not in st.session_state:
        st.session_state["filter_sequence_type"] = []
    if "filter_chip_atlas" not in st.session_state:
        st.session_state["filter_chip_atlas"] = "All"
    if "filter_year" not in st.session_state:
        st.session_state["filter_year"] = (year_min, year_max)

    selected_organisms = st.sidebar.multiselect(
        "Organism", options=organisms, key="filter_organism"
    )
    selected_sources = st.sidebar.multiselect(
        "Source system", options=sources, key="filter_source"
    )
    selected_seq_types: list[str] = []
    if seq_types:
        selected_seq_types = st.sidebar.multiselect(
            "Sequence type",
            options=seq_types,
            key="filter_sequence_type",
            help="ChIP-Atlas は experimentList.tab の track_type_class、"
            "rnaseq-human は source default で決まる",
        )
    chip_choice = st.sidebar.radio(
        "ChIP-Atlas",
        options=["All", "Only ChIP-Atlas", "Exclude ChIP-Atlas"],
        key="filter_chip_atlas",
        help="in_chip_atlas flag (source_system が chip-atlas-* かどうか) で絞り込む。"
        "細かい assay 種別で絞りたいときは Sequence type を使う。",
    )
    year_range = cast(
        tuple[int, int],
        st.sidebar.slider(
            "Submission year",
            min_value=year_min,
            max_value=year_max,
            key="filter_year",
        ),
    )

    in_chip_atlas: bool | None = None
    if chip_choice == "Only ChIP-Atlas":
        in_chip_atlas = True
    elif chip_choice == "Exclude ChIP-Atlas":
        in_chip_atlas = False

    return SampleFilters(
        organism_normalized=selected_organisms,
        submission_year_min=year_range[0] if year_range[0] > year_min else None,
        submission_year_max=year_range[1] if year_range[1] < year_max else None,
        source_system=selected_sources,
        in_chip_atlas=in_chip_atlas,
        sequence_type=selected_seq_types,
    )
