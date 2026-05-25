"""Cached DuckDB connection shared across Streamlit pages."""

from __future__ import annotations

import duckdb
import streamlit as st

from bsllmner_viewer.lib.duckdb import get_conn


@st.cache_resource(show_spinner=False)
def conn() -> duckdb.DuckDBPyConnection:
    return get_conn()
