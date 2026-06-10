from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    field_facts_status,
    field_facts_status_fast,
    has_dashboard_aggregates,
    samples_by_organism,
    samples_by_organism_fast,
    samples_by_sequence_type_fast,
    samples_by_source,
    samples_by_source_fast,
    samples_by_year_source,
    samples_by_year_source_fast,
    summary_counts_fast,
    top_terms_overall,
    top_terms_overall_fast,
)
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
    if has_dashboard_aggregates(c):
        return summary_counts_fast(c)
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


# Filter-free dashboard aggregates — cached separately so toggling Top-N field
# (F5) doesn't re-run F1/F2/F3. fast=True なら build-aggregates が生成した
# 小さい agg view を読む (cold-start ~10ms vs 13M facts scan)。
@st.cache_data(show_spinner=False)
def _yearly() -> pd.DataFrame:
    c = conn()
    if has_dashboard_aggregates(c):
        return samples_by_year_source_fast(c)
    return samples_by_year_source(c)


@st.cache_data(show_spinner=False)
def _organisms() -> pd.DataFrame:
    c = conn()
    if has_dashboard_aggregates(c):
        return samples_by_organism_fast(c)
    return samples_by_organism(c)


@st.cache_data(show_spinner=False)
def _sources() -> pd.DataFrame:
    c = conn()
    if has_dashboard_aggregates(c):
        return samples_by_source_fast(c)
    return samples_by_source(c)


@st.cache_data(show_spinner=False)
def _seq_types() -> pd.DataFrame | None:
    """sequence_type 別 sample 数 donut の data。agg 不在時は None。"""
    c = conn()
    if not has_dashboard_aggregates(c):
        return None
    return samples_by_sequence_type_fast(c)


@st.cache_data(show_spinner=False)
def _status_matrix() -> pd.DataFrame:
    c = conn()
    if has_dashboard_aggregates(c):
        return field_facts_status_fast(c)
    return field_facts_status(c)


@st.cache_data(show_spinner=False)
def _top_terms(field: str, top_n: int) -> pd.DataFrame:
    c = conn()
    if has_dashboard_aggregates(c):
        return top_terms_overall_fast(c, field, top_n)
    return top_terms_overall(c, field, top_n)


# F1: Yearly submission trend
st.subheader("Submissions over time")
yearly_df = _yearly()
if yearly_df.empty:
    st.caption("No submission_year data.")
else:
    fig_year = px.area(
        yearly_df,
        x="submission_year",
        y="sample_count",
        color="source_system",
        labels={
            "submission_year": "Submission year",
            "sample_count": "BioSample",
            "source_system": "Source",
        },
    )
    fig_year.update_layout(
        height=320,
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(fig_year, width="stretch", key="home_yearly")

# F2: Organism + Source + Sequence type donuts
st.subheader("Organism / source / sequence type split")
seq_df = _seq_types()
n_cols = 3 if seq_df is not None and not seq_df.empty else 2
cols = st.columns(n_cols)
with cols[0]:
    org_df = _organisms()
    if org_df.empty:
        st.caption("No organism data.")
    else:
        fig_org = px.pie(
            org_df,
            names="organism_normalized",
            values="sample_count",
            hole=0.5,
            title="Organism (normalized)",
        )
        fig_org.update_layout(
            height=320,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_org, width="stretch", key="home_org_donut")
with cols[1]:
    src_df = _sources()
    if src_df.empty:
        st.caption("No source data.")
    else:
        fig_src = px.pie(
            src_df,
            names="source_system",
            values="sample_count",
            hole=0.5,
            title="Source system",
        )
        fig_src.update_layout(
            height=320,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_src, width="stretch", key="home_src_donut")
if n_cols == 3 and seq_df is not None and not seq_df.empty:
    with cols[2]:
        fig_seq = px.pie(
            seq_df,
            names="sequence_type",
            values="sample_count",
            hole=0.5,
            title="Sequence type",
        )
        fig_seq.update_layout(
            height=320,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_seq, width="stretch", key="home_seq_donut")

# F3 + F4: Mapping success
st.subheader("Mapping quality")
status_df = _status_matrix()
if status_df.empty:
    st.caption("No facts loaded.")
else:
    total_n = int(status_df["n"].sum())
    by_status = (
        status_df.groupby("extract_status", as_index=False)["n"]
        .sum()
        .set_index("extract_status")["n"]
        .to_dict()
    )
    # F4: overall metric trio. Defaults to 0 when a status is absent so the
    # cards always render the same 3 columns.
    n_ok = int(by_status.get("ok", 0))
    n_map_fail = int(by_status.get("mapping_failed", 0))
    n_ext_fail = int(by_status.get("extract_failed", 0))
    col_ok, col_map, col_ext = st.columns(3)
    col_ok.metric(
        "ok",
        f"{n_ok:,}",
        f"{n_ok / total_n:.1%}" if total_n else "—",
    )
    col_map.metric(
        "mapping_failed",
        f"{n_map_fail:,}",
        f"{n_map_fail / total_n:.1%}" if total_n else "—",
    )
    col_ext.metric(
        "extract_failed",
        f"{n_ext_fail:,}",
        f"{n_ext_fail / total_n:.1%}" if total_n else "—",
    )

    # F3: per-field 100% stacked horizontal bar. Compute share within each
    # field so a sparse field (e.g. knockdown_gene) is comparable to a dense
    # one (disease) on the same chart.
    pivot = (
        status_df.pivot_table(
            index="field",
            columns="extract_status",
            values="n",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(columns=["ok", "mapping_failed", "extract_failed"], fill_value=0)
    )
    pivot["total"] = pivot.sum(axis=1)
    pivot = pivot[pivot["total"] > 0].sort_values("total", ascending=True)
    share = pivot[["ok", "mapping_failed", "extract_failed"]].div(
        pivot["total"], axis=0
    )
    long_df = share.reset_index().melt(
        id_vars="field", var_name="extract_status", value_name="share"
    )
    fig_status = px.bar(
        long_df,
        x="share",
        y="field",
        color="extract_status",
        orientation="h",
        category_orders={
            "field": list(pivot.index),
            "extract_status": ["ok", "mapping_failed", "extract_failed"],
        },
        color_discrete_map={
            "ok": "#2ca02c",
            "mapping_failed": "#ff7f0e",
            "extract_failed": "#d62728",
        },
        labels={"share": "Share", "field": "Field"},
    )
    fig_status.update_xaxes(tickformat=".0%", range=[0, 1])
    fig_status.update_layout(
        height=max(240, 36 * len(pivot.index)),
        barmode="stack",
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
        legend={"orientation": "h"},
    )
    st.plotly_chart(fig_status, width="stretch", key="home_status_stack")

# F5: Top 10 terms per field (Pareto)
st.subheader("Top terms")
col_field, _ = st.columns([1, 3])
with col_field:
    pareto_field = st.selectbox(
        "Field",
        options=VALID_FIELDS,
        index=VALID_FIELDS.index("disease"),
        key="home_pareto_field",
    )
top_df = _top_terms(pareto_field, 10)
if top_df.empty:
    st.caption(f"No `{pareto_field}` terms in the dataset.")
else:
    top_df = top_df.copy()
    top_df["display"] = top_df["label"].fillna(top_df["term_id"])
    fig_pareto = px.bar(
        top_df.sort_values("sample_count", ascending=True),
        x="sample_count",
        y="display",
        orientation="h",
        hover_data={"term_id": True, "display": False},
        labels={"sample_count": "BioSample", "display": pareto_field},
    )
    fig_pareto.update_layout(
        height=320,
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
        showlegend=False,
    )
    st.plotly_chart(fig_pareto, width="stretch", key="home_pareto")


@st.cache_data(show_spinner=False)
def _runs_table() -> object:
    return conn().execute(
        "SELECT run_name, source_system, model, status, total_entries, "
        "       error_count, start_time, end_time, processing_time_sec "
        "FROM runs "
        "ORDER BY start_time DESC"
    ).fetchdf()


st.subheader("Runs")
st.dataframe(_runs_table(), width="stretch", hide_index=True)

st.divider()

st.subheader("Pages")
st.markdown(
    """
- **Gap Discovery** — cross-tab heatmap of two ontology-mapped fields to surface empty cells.
- **Cohort** — drill into a chosen cell, download TSV, jump to DDBJ Search.
- **Gapminder** — animated bubble + rank race + heatmap + composition of (year × term × organism).
- **Curation** — LLM mapping quality dashboard (success rate, top unmapped values).
"""
)

st.caption(
    "Schemas: `docs/data-model.md` · UI design: `docs/ui.md` · ETL: `docs/etl.md`"
)
