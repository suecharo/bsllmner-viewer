from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    SampleFilters,
    bubble_dataset,
    cumulative_bubble_dataset,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters
from bsllmner_viewer.ui._term_popover import render_term_popover

st.set_page_config(page_title="Gapminder — bsllmner-viewer", layout="wide")

# Clicking *any* fullscreen toggle on a Plotly chart that uses
# `animation_frame` permanently breaks the animation slider (Plotly's autosize
# doesn't recover after the wrapper round-trip). There are two distinct
# fullscreen entry points to suppress:
#   1. Streamlit's hover-only "View fullscreen" overlay icon
#   2. Plotly's own modebar Fullscreen button (data-title="Fullscreen")
# We hide both via CSS and also strip "fullscreen" from Plotly's modebar via
# fig.update_layout(modebar=...) further down. CSS is the reliable backstop —
# Plotly versions vary on the button's internal name.
st.markdown(
    """
    <style>
    [data-testid="stElementToolbar"],
    [data-testid="stElementToolbarButton"],
    button[title="View fullscreen"],
    button[aria-label="View fullscreen"],
    button.modebar-btn[data-title="Fullscreen"],
    button.modebar-btn[aria-label="Fullscreen"] {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Gapminder bubble")
st.caption(
    "Bubble / line view of (submission_year × top ontology terms × organism). "
    "Toggle Cumulative vs Per-year and log vs linear axes — every switch "
    "re-derives axis range, size scaling, and category order from scratch."
)

con = conn()
filters: SampleFilters = sidebar_filters(con)

col_f, col_n = st.columns([3, 1])
with col_f:
    field_name = st.selectbox(
        "Field", options=VALID_FIELDS, index=VALID_FIELDS.index("disease")
    )
with col_n:
    top_n = st.slider("Top N terms", min_value=5, max_value=50, value=15, step=5)

col_m, col_y, col_s = st.columns(3)
with col_m:
    mode = st.radio(
        "Aggregation",
        options=["Cumulative", "Per-year"],
        index=0,
        horizontal=True,
        help=(
            "Cumulative = running total per (term, organism) up to that year "
            "(bubble grows over time). Per-year = only new samples in that year."
        ),
    )
with col_y:
    y_log = st.toggle("Log scale Y axis", value=True)
with col_s:
    size_log = st.toggle(
        "Log scale bubble size",
        value=True,
        help=(
            "When on, marker size is mapped from log10(count). When off, marker "
            "size is mapped linearly, so the largest bubble can dwarf the rest."
        ),
    )


@st.cache_data(show_spinner="aggregating…")
def _load(
    mode: str,
    field_name: str,
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
    if mode == "Cumulative":
        df = cumulative_bubble_dataset(conn(), field_name, f, top_n)
        df["count"] = df["sample_count_cum"]
        df["chip_count"] = df["chip_atlas_count_cum"]
    else:
        df = bubble_dataset(conn(), field_name, f, top_n)
        df["count"] = df["sample_count"]
        df["chip_count"] = df["chip_atlas_count"]
    return df


df = _load(
    mode,
    field_name,
    top_n,
    tuple(filters.organism_normalized),
    tuple(filters.source_system),
    filters.submission_year_min,
    filters.submission_year_max,
    filters.in_chip_atlas,
)

if df.empty:
    st.warning("No data for the current filters / field.")
    st.stop()

# Re-derive every display parameter from the freshly loaded df. Doing it here
# (rather than inside @st.cache_data) means a toggle flip rebuilds axis range,
# size scaling, and category order even when the underlying data is the same.
df = df.copy()
df["label"] = df["label"].fillna(df["term_id"])
df["submission_year"] = df["submission_year"].astype(int)
df["trace_key"] = df["label"] + " | " + df["organism_normalized"].fillna("?")

if size_log:
    df["bubble_size"] = np.log10(df["count"].clip(lower=1)) + 1
else:
    df["bubble_size"] = df["count"].clip(lower=0)

# Pin every term to a fixed numeric x position so the axis order is identical
# across animation frames. Plotly's `category_orders` is unreliable when
# animation_frame subsets drop a category.
category_order = (
    df.groupby("label")["count"].max().sort_values(ascending=False).index.tolist()
)
label_to_x = {lbl: i for i, lbl in enumerate(category_order)}
df["x_pos"] = df["label"].map(label_to_x)

# Cache-bust the Plotly elements when any of the display knobs flip so Plotly
# rebuilds the figure DOM with the new range / scale / category order rather
# than reusing the previous frame.
chart_state_key = (
    f"{mode}|{field_name}|{top_n}|{y_log}|{size_log}|"
    f"{tuple(filters.organism_normalized)}|{tuple(filters.source_system)}|"
    f"{filters.submission_year_min}|{filters.submission_year_max}|"
    f"{filters.in_chip_atlas}"
)

count_axis_title = (
    "BioSample count (cumulative)" if mode == "Cumulative"
    else "BioSample count (per-year)"
)
chip_axis_title = (
    "ChIP-Atlas (cumulative)" if mode == "Cumulative"
    else "ChIP-Atlas (per-year)"
)

tab_bubble, tab_line = st.tabs(["Bubble", "Trajectory"])

with tab_bubble:
    bubble_df = df[df["count"] > 0].copy()
    if bubble_df.empty:
        st.info(
            "All cells are zero for the current selection. "
            "Switch *Aggregation* to Cumulative or relax the filters."
        )
    else:
        max_count = float(bubble_df["count"].max())
        range_y = (
            [0.8, max(2.0, max_count * 1.4)]
            if y_log
            else [0, max(1.0, max_count * 1.1)]
        )

        fig = px.scatter(
            bubble_df,
            x="x_pos",
            y="count",
            size="bubble_size",
            size_max=28,
            color="organism_normalized",
            animation_frame="submission_year",
            animation_group="trace_key",
            hover_name="label",
            hover_data={
                "term_id": True,
                "count": True,
                "chip_count": True,
                "bubble_size": False,
                "trace_key": False,
                "submission_year": False,
                "x_pos": False,
            },
            log_y=y_log,
            range_y=range_y,
            range_x=[-0.5, len(category_order) - 0.5],
            labels={
                "count": count_axis_title,
                "chip_count": chip_axis_title,
                "organism_normalized": "Organism",
            },
        )
        # Don't override `sizemode` here. Plotly Express's `size_max` sets the
        # sizeref assuming sizemode="area" (the default); flipping sizemode to
        # diameter after the fact would leave sizeref at the area-scale value
        # (max_size / size_max**2 ≈ 0.0067) and a leaf bubble with size=1.3
        # would render at ~195 px diameter.
        fig.update_xaxes(
            tickmode="array",
            tickvals=list(range(len(category_order))),
            ticktext=category_order,
            tickangle=-30,
            title=field_name,
        )
        fig.update_layout(
            height=600,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        # Slow down the default animation so the bubble move is readable.
        if fig.layout.updatemenus:
            fig.layout.updatemenus[0]["buttons"][0]["args"][1]["frame"][
                "duration"
            ] = 800
            fig.layout.updatemenus[0]["buttons"][0]["args"][1]["transition"][
                "duration"
            ] = 400
        st.plotly_chart(fig, width="stretch", key=f"bubble_{chart_state_key}")
        st.caption(
            f"{len(bubble_df)} (year × term × organism) cells across "
            f"{bubble_df['submission_year'].nunique()} years "
            f"(zero-count cells hidden; see *Trajectory* tab for the full grid)."
        )

with tab_line:
    line_df = df.sort_values(["trace_key", "submission_year"])
    fig_line = px.line(
        line_df,
        x="submission_year",
        y="count",
        color="label",
        line_dash="organism_normalized",
        hover_data={
            "term_id": True,
            "chip_count": True,
            "organism_normalized": True,
        },
        markers=True,
        log_y=y_log,
        labels={
            "submission_year": "Submission year",
            "count": count_axis_title,
            "chip_count": chip_axis_title,
            "label": field_name,
            "organism_normalized": "Organism",
        },
    )
    fig_line.update_layout(
        height=600,
        legend={"orientation": "v"},
        modebar={"remove": ["fullscreen", "togglefullscreen"]},
    )
    st.plotly_chart(fig_line, width="stretch", key=f"line_{chart_state_key}")
    st.caption(
        f"{line_df['trace_key'].nunique()} traces across "
        f"{line_df['submission_year'].nunique()} years. "
        f"Y axis: {'log' if y_log else 'linear'} · "
        f"Mode: {mode.lower()}."
    )

# Show the Top N (term_id, label) selection from the underlying dataset and
# expose each one as a term-info popover. The bubble chart itself can't be
# wired to a click handler, so this section is the navigation hand-off.
st.subheader(f"Top terms for `{field_name}`")
term_label_map = (
    df.drop_duplicates("term_id").set_index("term_id")["label"].to_dict()
)
for term_id, lbl in term_label_map.items():
    render_term_popover(con, field_name, term_id, lbl, filters)
