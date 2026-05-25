from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    SampleFilters,
    gap_heatmap_pivot,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters

st.set_page_config(page_title="Gap Discovery — bsllmner-viewer", layout="wide")

st.title("Gap Discovery")
st.caption(
    "Heatmap of BioSample count across two ontology-mapped fields. "
    "Empty cells = candidate gaps to fill with future submissions."
)

con = conn()
filters: SampleFilters = sidebar_filters(con)

col_x, col_y, col_n = st.columns([2, 2, 1])
with col_x:
    x_field = st.selectbox(
        "X axis (field)", options=VALID_FIELDS, index=VALID_FIELDS.index("disease")
    )
with col_y:
    default_y = "drug" if "drug" in VALID_FIELDS else VALID_FIELDS[0]
    y_field = st.selectbox(
        "Y axis (field)", options=VALID_FIELDS, index=VALID_FIELDS.index(default_y)
    )
with col_n:
    top_n = st.slider("Top N per axis", min_value=5, max_value=50, value=20, step=5)


@st.cache_data(show_spinner="aggregating…")
def _pivot(
    x_field: str,
    y_field: str,
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
    return gap_heatmap_pivot(conn(), x_field, y_field, f, top_n, top_n)


df = _pivot(
    x_field,
    y_field,
    top_n,
    tuple(filters.organism_normalized),
    tuple(filters.source_system),
    filters.submission_year_min,
    filters.submission_year_max,
    filters.in_chip_atlas,
)

if df.empty:
    st.warning("No data for the current filters / fields.")
    st.stop()

pivot_counts = (
    df.pivot_table(
        index="y_label",
        columns="x_label",
        values="sample_count",
        aggfunc="sum",
        fill_value=0,
    )
    .sort_index(axis=0)
    .sort_index(axis=1)
)
overlay_counts = (
    df.pivot_table(
        index="y_label",
        columns="x_label",
        values="chip_atlas_count",
        aggfunc="sum",
        fill_value=0,
    )
    .reindex(index=pivot_counts.index, columns=pivot_counts.columns, fill_value=0)
)

custom = overlay_counts.to_numpy()
fig = px.imshow(
    pivot_counts,
    aspect="auto",
    color_continuous_scale="Blues",
    labels={"x": x_field, "y": y_field, "color": "BioSample count"},
)
fig.update_traces(
    customdata=custom,
    hovertemplate=(
        f"{x_field}: %{{x}}<br>"
        f"{y_field}: %{{y}}<br>"
        "BioSample: %{z}<br>"
        "of which ChIP-Atlas: %{customdata}<extra></extra>"
    ),
)
fig.update_layout(height=max(400, 30 * len(pivot_counts.index)))

st.plotly_chart(fig, use_container_width=True)

st.subheader("Drill into a cell")
st.caption(
    "Pick an x / y term to send the matched cohort to the Cohort page. "
    "Cell click on the heatmap is not yet wired up — use the selectors below."
)

term_lookup = (
    df.groupby(["x_term_id", "x_label"]).size().reset_index().rename(columns={0: "n"})
)
y_lookup = (
    df.groupby(["y_term_id", "y_label"]).size().reset_index().rename(columns={0: "n"})
)
x_options = list(zip(term_lookup["x_term_id"], term_lookup["x_label"], strict=True))
y_options = list(zip(y_lookup["y_term_id"], y_lookup["y_label"], strict=True))

cell_col1, cell_col2, cell_col3 = st.columns([2, 2, 1])
with cell_col1:
    chosen_x = st.selectbox(
        f"{x_field} term",
        options=x_options,
        format_func=lambda p: f"{p[1]} ({p[0]})",
    )
with cell_col2:
    chosen_y = st.selectbox(
        f"{y_field} term",
        options=y_options,
        format_func=lambda p: f"{p[1]} ({p[0]})",
    )
with cell_col3:
    if st.button("Open in Cohort →", use_container_width=True):
        st.session_state["cohort_filters"] = filters
        st.session_state["cohort_facts_terms"] = [
            (x_field, chosen_x[0]),
            (y_field, chosen_y[0]),
        ]
        st.session_state["cohort_label"] = (
            f"{x_field}={chosen_x[1]} × {y_field}={chosen_y[1]}"
        )
        st.switch_page("pages/02_Cohort.py")
