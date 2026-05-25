from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    FIELD_TO_ONTOLOGY,
    VALID_FIELDS,
    SampleFilters,
    can_roll_up,
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


def _depth_picker(label: str, field: str, key: str) -> int | None:
    if not can_roll_up(field):
        st.caption(
            f"`{field}` rolls up against no usable hierarchy "
            "(Cellosaurus / NCBI Gene) — depth picker disabled."
        )
        return None
    source = FIELD_TO_ONTOLOGY[field]
    choices = ["leaf", "0", "1", "2", "3", "4", "5"]
    chosen = st.selectbox(
        f"{label} ({source})",
        options=choices,
        index=0,
        key=key,
        help="Roll each leaf term up to its ancestor at the chosen depth. "
        "`leaf` keeps the original term.",
    )
    return None if chosen == "leaf" else int(chosen)


col_xd, col_yd = st.columns(2)
with col_xd:
    x_roll_up_depth = _depth_picker("X roll-up depth", x_field, "x_depth_picker")
with col_yd:
    y_roll_up_depth = _depth_picker("Y roll-up depth", y_field, "y_depth_picker")


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
    x_roll_up_depth: int | None,
    y_roll_up_depth: int | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=list(organism),
        source_system=list(source),
        submission_year_min=year_min,
        submission_year_max=year_max,
        in_chip_atlas=in_chip_atlas,
    )
    return gap_heatmap_pivot(
        conn(),
        x_field,
        y_field,
        f,
        top_n,
        top_n,
        x_roll_up_depth=x_roll_up_depth,
        y_roll_up_depth=y_roll_up_depth,
    )


df = _pivot(
    x_field,
    y_field,
    top_n,
    tuple(filters.organism_normalized),
    tuple(filters.source_system),
    filters.submission_year_min,
    filters.submission_year_max,
    filters.in_chip_atlas,
    x_roll_up_depth,
    y_roll_up_depth,
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

# Overlay an invisible scatter trace so Streamlit's on_select="rerun" + box /
# lasso selection mode can map clicks back to (x_label, y_label). Plotly's
# Heatmap trace alone doesn't emit point selection events through Streamlit's
# selection API — point-mode click on the imshow surface never fires
# `selection.points`. Box / lasso select on the overlay does work and lets the
# user pick a single (or multi-) cell visually.
overlay_rows = [
    {"x": x_lbl, "y": y_lbl}
    for y_lbl in pivot_counts.index
    for x_lbl in pivot_counts.columns
]
overlay_df = pd.DataFrame(overlay_rows)
fig.add_scatter(
    x=overlay_df["x"],
    y=overlay_df["y"],
    mode="markers",
    marker={"size": 22, "color": "rgba(0,0,0,0)", "line": {"width": 0}},
    hoverinfo="skip",
    showlegend=False,
    name="cell-select",
)

fig.update_layout(height=max(400, 30 * len(pivot_counts.index)))

chart_event: object = st.plotly_chart(
    fig,
    width="stretch",
    key="gap_heatmap",
    on_select="rerun",
    selection_mode=("points", "box"),
)

x_label_to_id = dict(zip(df["x_label"], df["x_term_id"], strict=False))
y_label_to_id = dict(zip(df["y_label"], df["y_term_id"], strict=False))

# Streamlit's PlotlyState is dict-like: {'selection': {'points': [{...}, ...]}}.
# Access dynamically to avoid leaking PlotlyState typing into our code.
selection = (
    chart_event["selection"]
    if isinstance(chart_event, dict) and "selection" in chart_event
    else None
)
points: list[dict[str, object]] = (
    selection.get("points", [])
    if isinstance(selection, dict)
    else []
)
selected_labels: list[tuple[str, str]] = []
for p in points:
    x_val = p.get("x")
    y_val = p.get("y")
    if isinstance(x_val, str) and isinstance(y_val, str):
        selected_labels.append((x_val, y_val))

st.subheader("Drill into a cell")
st.caption(
    "Use the box-select tool on the heatmap toolbar to pick one or more cells, "
    "or set the term selectors below directly. The first selected cell is "
    "carried into the Cohort drill-down."
)

x_options = list(x_label_to_id.items())  # [(label, term_id), ...]
y_options = list(y_label_to_id.items())

x_default_idx = 0
y_default_idx = 0
if selected_labels:
    first_x, first_y = selected_labels[0]
    if first_x in x_label_to_id:
        x_default_idx = [lbl for lbl, _ in x_options].index(first_x)
    if first_y in y_label_to_id:
        y_default_idx = [lbl for lbl, _ in y_options].index(first_y)

cell_col1, cell_col2, cell_col3 = st.columns([2, 2, 1])
with cell_col1:
    chosen_x = st.selectbox(
        f"{x_field} term",
        options=x_options,
        index=x_default_idx,
        format_func=lambda p: f"{p[0]} ({p[1]})",
    )
with cell_col2:
    chosen_y = st.selectbox(
        f"{y_field} term",
        options=y_options,
        index=y_default_idx,
        format_func=lambda p: f"{p[0]} ({p[1]})",
    )
with cell_col3:
    if st.button("Open in Cohort →", width="stretch"):
        st.session_state["cohort_filters"] = filters
        st.session_state["cohort_facts_terms"] = [
            (x_field, chosen_x[1]),
            (y_field, chosen_y[1]),
        ]
        st.session_state["cohort_label"] = (
            f"{x_field}={chosen_x[0]} × {y_field}={chosen_y[0]}"
        )
        st.session_state.pop("cohort_seeded", None)
        st.switch_page("pages/02_Cohort.py")

if selected_labels:
    st.success(
        f"{len(selected_labels)} cell(s) selected from heatmap — selectors "
        f"primed to the first one ({selected_labels[0][0]} × "
        f"{selected_labels[0][1]}). Click *Open in Cohort* to drill in."
    )
