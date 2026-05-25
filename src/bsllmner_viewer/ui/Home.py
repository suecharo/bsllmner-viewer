from __future__ import annotations

import streamlit as st

from bsllmner_viewer.ui._conn import conn

st.set_page_config(page_title="bsllmner-viewer", layout="wide")

st.title("bsllmner-viewer")
st.caption(
    "Browse BioSample × ontology mapping results produced by bsllmner-mk2."
)

con = conn()


@st.cache_data(show_spinner=False)
def _summary() -> dict[str, int]:
    c = conn()
    n_samples = c.execute("SELECT COUNT(*) FROM samples").fetchone()
    n_runs = c.execute("SELECT COUNT(*) FROM runs").fetchone()
    n_facts = c.execute("SELECT COUNT(*) FROM facts").fetchone()
    n_chip = c.execute(
        "SELECT COUNT(*) FROM samples WHERE in_chip_atlas"
    ).fetchone()
    n_terms = c.execute(
        "SELECT COUNT(DISTINCT term_id) FROM ontology"
    ).fetchone()
    return {
        "samples": int(n_samples[0]) if n_samples else 0,
        "runs": int(n_runs[0]) if n_runs else 0,
        "facts": int(n_facts[0]) if n_facts else 0,
        "chip_atlas": int(n_chip[0]) if n_chip else 0,
        "terms": int(n_terms[0]) if n_terms else 0,
    }


summary = _summary()

col_a, col_b, col_c, col_d, col_e = st.columns(5)
col_a.metric("Runs", f"{summary['runs']:,}")
col_b.metric("BioSamples", f"{summary['samples']:,}")
col_c.metric("of which ChIP-Atlas", f"{summary['chip_atlas']:,}")
col_d.metric("Facts (long)", f"{summary['facts']:,}")
col_e.metric("Ontology terms", f"{summary['terms']:,}")


@st.cache_data(show_spinner=False)
def _runs_table() -> object:
    return conn().execute(
        "SELECT run_name, source_system, model, status, total_entries, "
        "       error_count, start_time, end_time, processing_time_sec "
        "FROM runs "
        "ORDER BY start_time DESC"
    ).fetchdf()


st.subheader("Runs")
st.dataframe(_runs_table(), use_container_width=True, hide_index=True)

st.divider()

st.subheader("Pages")
st.markdown(
    """
- **Gap Discovery** — cross-tab heatmap of two ontology-mapped fields to surface empty cells.
- **Cohort** — drill into a chosen cell, download TSV, jump to DDBJ Search.
- **Gapminder** — animated bubble of (year × term × organism).
"""
)

st.caption(
    "Schemas: `docs/data-model.md` · UI design: `docs/ui.md` · ETL: `docs/etl.md`"
)
