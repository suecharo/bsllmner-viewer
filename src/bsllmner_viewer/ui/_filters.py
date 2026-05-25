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
    chip_choice = st.sidebar.radio(
        "ChIP-Atlas",
        options=["All", "Only ChIP-Atlas", "Exclude ChIP-Atlas"],
        key="filter_chip_atlas",
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
    )
