from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    SampleFilters,
    mapping_status_matrix,
    mapping_status_over_time,
    raw_value_term_flow,
    top_unmapped_values,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters

st.set_page_config(page_title="Curation — bsllmner-viewer", layout="wide")

st.title("Curation report")
st.caption(
    "LLM extraction + ontology mapping quality. The sidebar filters apply: "
    "narrow by year / source / organism to see how the curation gap shifts "
    "in a subset."
)

con = conn()
filters: SampleFilters = sidebar_filters(con)


@st.cache_data(show_spinner="aggregating…")
def _status_matrix(
    organism: tuple[str, ...],
    source: tuple[str, ...],
    year_min: int | None,
    year_max: int | None,
    in_chip_atlas: bool | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=list(organism),
        source_system=list(source),
        submission_year_min=year_min,
        submission_year_max=year_max,
        in_chip_atlas=in_chip_atlas,
    )
    return mapping_status_matrix(conn(), f)


@st.cache_data(show_spinner="aggregating…")
def _status_over_time(
    organism: tuple[str, ...],
    source: tuple[str, ...],
    year_min: int | None,
    year_max: int | None,
    in_chip_atlas: bool | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=list(organism),
        source_system=list(source),
        submission_year_min=year_min,
        submission_year_max=year_max,
        in_chip_atlas=in_chip_atlas,
    )
    return mapping_status_over_time(conn(), f)


@st.cache_data(show_spinner="aggregating…")
def _unmapped(
    field: str,
    top_n: int,
    organism: tuple[str, ...],
    source: tuple[str, ...],
    year_min: int | None,
    year_max: int | None,
    in_chip_atlas: bool | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=list(organism),
        source_system=list(source),
        submission_year_min=year_min,
        submission_year_max=year_max,
        in_chip_atlas=in_chip_atlas,
    )
    return top_unmapped_values(conn(), field, top_n, f)


filter_key = (
    tuple(filters.organism_normalized),
    tuple(filters.source_system),
    filters.submission_year_min,
    filters.submission_year_max,
    filters.in_chip_atlas,
)

# D1: Mapping status heatmap (field × source_system) with ok-rate color.
st.subheader("Mapping status — field × source")
matrix = _status_matrix(*filter_key)
if matrix.empty:
    st.info("No facts under the current filters.")
else:
    totals = matrix.groupby(["field", "source_system"], as_index=False)["n"].sum()
    ok_rows = matrix[matrix["extract_status"] == "ok"].groupby(
        ["field", "source_system"], as_index=False
    )["n"].sum()
    merged = totals.merge(
        ok_rows.rename(columns={"n": "n_ok"}),
        on=["field", "source_system"],
        how="left",
    )
    merged["n_ok"] = merged["n_ok"].fillna(0).astype(int)
    merged["ok_rate"] = merged["n_ok"] / merged["n"]
    ok_pivot = (
        merged.pivot(index="field", columns="source_system", values="ok_rate")
        .sort_index(axis=0)
        .sort_index(axis=1)
    )
    total_pivot = (
        merged.pivot(index="field", columns="source_system", values="n")
        .reindex(index=ok_pivot.index, columns=ok_pivot.columns)
        .fillna(0)
        .astype(int)
    )
    # ok-rate as color, hover shows ok count and total fact rows.
    custom = total_pivot.to_numpy()
    fig_d1 = px.imshow(
        ok_pivot,
        aspect="auto",
        color_continuous_scale="RdYlGn",
        zmin=0.0,
        zmax=1.0,
        labels={"x": "source_system", "y": "field", "color": "ok rate"},
    )
    fig_d1.update_traces(
        customdata=custom,
        hovertemplate=(
            "field: %{y}<br>"
            "source: %{x}<br>"
            "ok rate: %{z:.1%}<br>"
            "total facts: %{customdata}<extra></extra>"
        ),
    )
    fig_d1.update_layout(
        height=max(360, 32 * len(ok_pivot.index)),
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(fig_d1, width="stretch", key=f"d1_{filter_key}")
    st.caption(
        "Color = share of facts whose extract_status is `ok` "
        "(green = curation-clean, red = many failures). "
        "Hover for raw counts."
    )

st.divider()

# D2: Mapping success over time (multi-line per field).
st.subheader("Mapping success over time")
time_df = _status_over_time(*filter_key)
if time_df.empty:
    st.info("No time-series data under the current filters.")
else:
    per_year_total = time_df.groupby(["field", "submission_year"], as_index=False)["n"].sum()
    per_year_ok = (
        time_df[time_df["extract_status"] == "ok"]
        .groupby(["field", "submission_year"], as_index=False)["n"]
        .sum()
    )
    merged_t = per_year_total.merge(
        per_year_ok.rename(columns={"n": "n_ok"}),
        on=["field", "submission_year"],
        how="left",
    )
    merged_t["n_ok"] = merged_t["n_ok"].fillna(0).astype(int)
    merged_t["ok_rate"] = merged_t["n_ok"] / merged_t["n"]
    merged_t["submission_year"] = merged_t["submission_year"].astype(int)
    merged_t = merged_t.sort_values(["field", "submission_year"])

    fig_d2 = px.line(
        merged_t,
        x="submission_year",
        y="ok_rate",
        color="field",
        markers=True,
        hover_data={"n": True, "n_ok": True, "ok_rate": ":.1%"},
        labels={
            "submission_year": "Submission year",
            "ok_rate": "ok rate",
            "field": "Field",
        },
    )
    fig_d2.update_yaxes(tickformat=".0%", range=[0, 1])
    fig_d2.update_layout(
        height=420,
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(fig_d2, width="stretch", key=f"d2_{filter_key}")
    st.caption(
        "Per-field `ok` rate over submission years. A rising line means "
        "curation (prompt + ontology) is catching up with the input vocabulary."
    )

st.divider()

# D3: Top N unmapped raw values for one field.
st.subheader("Top unmapped raw values")
col_field, col_n = st.columns([2, 1])
with col_field:
    unmap_field = st.selectbox(
        "Field",
        options=VALID_FIELDS,
        index=VALID_FIELDS.index("disease"),
        key="curation_unmap_field",
    )
with col_n:
    unmap_n = st.slider(
        "Top N",
        min_value=5,
        max_value=50,
        value=20,
        step=5,
        key="curation_unmap_n",
    )

unmap_df = _unmapped(unmap_field, unmap_n, *filter_key)
if unmap_df.empty:
    st.info(
        f"No `mapping_failed` rows for `{unmap_field}` under the current filters. "
        "Either every value mapped, or the filters excluded all failure rows."
    )
else:
    unmap_df = unmap_df.sort_values("n", ascending=True)
    fig_d3 = px.bar(
        unmap_df,
        x=["n", "sample_count"],
        y="value",
        orientation="h",
        barmode="group",
        labels={
            "value": f"raw {unmap_field} value",
            "variable": "metric",
            "value_x": "count",
        },
    )
    fig_d3.update_layout(
        height=max(320, 22 * len(unmap_df)),
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
        legend={"orientation": "h", "title": ""},
    )
    st.plotly_chart(fig_d3, width="stretch", key=f"d3_{filter_key}_{unmap_field}_{unmap_n}")
    st.caption(
        "`n` = fact-row frequency, `sample_count` = distinct BioSamples affected "
        "(diverges from `n` for array-typed fields like drug / knockout_gene)."
    )

st.divider()

# D4: Run timeline (Gantt). Each run is a bar; color = status. Filters do not
# apply — runs are a meta layer over the whole pipeline, not a sample subset.
st.subheader("Run timeline")
runs_df = con.execute(
    "SELECT run_name, source_system, model, start_time, end_time, status, "
    "       total_entries, error_count, processing_time_sec "
    "FROM runs ORDER BY start_time"
).fetchdf()
if runs_df.empty:
    st.info("No runs recorded.")
else:
    runs_df = runs_df.copy()
    runs_df["start_time"] = pd.to_datetime(runs_df["start_time"], utc=True)
    runs_df["end_time"] = pd.to_datetime(runs_df["end_time"], utc=True)
    # For still-running runs (end_time NULL) synthesize a finish time from
    # processing_time_sec, or fall back to start_time + 1 minute so the bar
    # is at least visible on the timeline.
    fallback_end = runs_df["start_time"] + pd.to_timedelta(
        runs_df["processing_time_sec"].fillna(0), unit="s"
    )
    no_duration = (runs_df["processing_time_sec"].isna()) | (
        runs_df["processing_time_sec"] == 0
    )
    fallback_end = fallback_end.where(~no_duration, runs_df["start_time"] + timedelta(minutes=1))
    runs_df["end_time"] = runs_df["end_time"].fillna(fallback_end)

    status_colors = {
        "completed": "#2ca02c",
        "failed": "#d62728",
        "interrupted": "#ff7f0e",
        "running": "#1f77b4",
    }
    fig_d4 = px.timeline(
        runs_df,
        x_start="start_time",
        x_end="end_time",
        y="run_name",
        color="status",
        color_discrete_map=status_colors,
        hover_data={
            "source_system": True,
            "model": True,
            "total_entries": True,
            "error_count": True,
            "processing_time_sec": ":.1f",
            "status": True,
        },
        labels={
            "run_name": "Run",
            "source_system": "Source",
            "model": "Model",
            "total_entries": "Entries",
            "error_count": "Errors",
            "processing_time_sec": "Duration (s)",
        },
    )
    fig_d4.update_yaxes(autorange="reversed")
    fig_d4.update_layout(
        height=max(360, 22 * len(runs_df)),
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(fig_d4, width="stretch", key="d4_runs")
    st.caption(
        "Each bar spans `start_time` → `end_time`. Bars with NULL end_time are "
        "synthesized from `processing_time_sec` (or +1 minute if absent) so "
        "the bar is still visible."
    )

st.divider()

# D5: Raw value → term Sankey. ok-mapped (value, term_id) pairs as flow.
# Reveals "1-to-many" raw values (one value mapped to many terms — likely
# false positives in the prompt) and "many-to-1" terms (many spellings
# collapsing onto one term — the curated language).
st.subheader("Raw value → term mapping")
col_f, col_top, col_min = st.columns([2, 1, 1])
with col_f:
    flow_field = st.selectbox(
        "Field",
        options=VALID_FIELDS,
        index=VALID_FIELDS.index("disease"),
        key="curation_flow_field",
    )
with col_top:
    flow_top = st.slider(
        "Top N pairs",
        min_value=10,
        max_value=100,
        value=30,
        step=5,
        key="curation_flow_top",
    )
with col_min:
    flow_min = st.slider(
        "Min count",
        min_value=1,
        max_value=20,
        value=1,
        key="curation_flow_min",
        help="Drop (value, term) links with fewer than this many facts.",
    )


@st.cache_data(show_spinner="aggregating…")
def _flow(
    field: str,
    top_n: int,
    min_count: int,
    organism: tuple[str, ...],
    source: tuple[str, ...],
    year_min: int | None,
    year_max: int | None,
    in_chip_atlas: bool | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=list(organism),
        source_system=list(source),
        submission_year_min=year_min,
        submission_year_max=year_max,
        in_chip_atlas=in_chip_atlas,
    )
    return raw_value_term_flow(conn(), field, top_n, f, min_count=min_count)


flow_df = _flow(flow_field, flow_top, flow_min, *filter_key)
if flow_df.empty:
    st.info(
        f"No `ok`-mapped (value, term) rows for `{flow_field}` under the "
        "current filters / min count."
    )
else:
    # Namespaced node names so left/right collisions are impossible (a raw
    # value string could theoretically collide with a term label).
    left_nodes = list(dict.fromkeys(flow_df["value"]))
    right_keys = list(
        dict.fromkeys(zip(flow_df["term_id"], flow_df["label"], strict=True))
    )
    node_names: list[str] = []
    node_colors: list[str] = []
    left_idx: dict[str, int] = {}
    right_idx: dict[str, int] = {}
    for raw in left_nodes:
        left_idx[raw] = len(node_names)
        node_names.append(f"value: {raw}")
        node_colors.append("rgba(31,119,180,0.8)")
    for term_id, label in right_keys:
        key = str(term_id)
        right_idx[key] = len(node_names)
        display = f"{label} ({term_id})" if label else str(term_id)
        node_names.append(f"term: {display}")
        node_colors.append("rgba(44,160,44,0.8)")

    sources = [left_idx[str(r)] for r in flow_df["value"]]
    targets = [right_idx[str(r)] for r in flow_df["term_id"]]
    values = flow_df["n"].astype(int).tolist()
    link_hover = [
        (
            f"raw: {r['value']}<br>"
            f"term: {r.get('label') or r['term_id']} ({r['term_id']})<br>"
            f"facts: {int(r['n'])}<br>"
            f"BioSamples: {int(r['sample_count'])}"
        )
        for _, r in flow_df.iterrows()
    ]

    fig_d5 = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node={
                    "label": node_names,
                    "color": node_colors,
                    "pad": 10,
                    "thickness": 16,
                },
                link={
                    "source": sources,
                    "target": targets,
                    "value": values,
                    "customdata": link_hover,
                    "hovertemplate": "%{customdata}<extra></extra>",
                },
            )
        ]
    )
    fig_d5.update_layout(
        height=max(420, 18 * max(len(left_nodes), len(right_keys))),
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(
        fig_d5,
        width="stretch",
        key=f"d5_{filter_key}_{flow_field}_{flow_top}_{flow_min}",
    )
    st.caption(
        "Left = raw extracted values, right = ontology terms. A value with "
        "many outgoing links = mapping is ambiguous; a term with many "
        "incoming links = language is fragmented (curation candidate)."
    )
