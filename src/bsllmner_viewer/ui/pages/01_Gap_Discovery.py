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
from bsllmner_viewer.ui._term_popover import render_term_popover

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

fig = px.imshow(
    pivot_counts,
    aspect="auto",
    color_continuous_scale="Blues",
    labels={"x": x_field, "y": y_field, "color": "BioSample count"},
)

# Overlay one transparent square per heatmap cell so a single click maps back
# to (x_label, y_label) — Plotly's Heatmap trace alone doesn't surface point
# selection events through Streamlit's selection API. The overlay also owns
# the hover tooltip: since it sits on top of the heatmap, the heatmap's own
# hovertemplate would otherwise be masked. We match the marker shape/size so
# the hit + hover area covers (almost) the full cell.
overlay_x: list[str] = []
overlay_y: list[str] = []
overlay_custom: list[list[int]] = []
for y_lbl in pivot_counts.index:
    for x_lbl in pivot_counts.columns:
        overlay_x.append(x_lbl)
        overlay_y.append(y_lbl)
        overlay_custom.append(
            [
                int(pivot_counts.loc[y_lbl, x_lbl]),
                int(overlay_counts.loc[y_lbl, x_lbl]),
            ]
        )
fig.add_scatter(
    x=overlay_x,
    y=overlay_y,
    mode="markers",
    marker={
        "symbol": "square",
        "size": 36,
        "color": "rgba(0,0,0,0)",
        "line": {"width": 0},
    },
    customdata=overlay_custom,
    hovertemplate=(
        f"{x_field}: %{{x}}<br>"
        f"{y_field}: %{{y}}<br>"
        "BioSample: %{customdata[0]}<br>"
        "of which ChIP-Atlas: %{customdata[1]}<extra></extra>"
    ),
    showlegend=False,
    name="cell-select",
)

fig.update_layout(
    height=max(400, 30 * len(pivot_counts.index)),
    dragmode="pan",
    clickmode="event+select",
    modebar={"remove": ["fullscreen", "togglefullscreen", "select", "lasso2d"]},
)

# Embed the axes and a "clear" nonce in the chart key so axis changes and the
# Clear button both yield a fresh chart instance with no carried-over click
# selection (otherwise Streamlit would replay the stale selection on rerun and
# re-add the cell to the table).
clear_nonce = st.session_state.get("gap_heatmap_clear_nonce", 0)
chart_key = f"gap_heatmap__{x_field}__{y_field}__{clear_nonce}"
chart_event: object = st.plotly_chart(
    fig,
    width="stretch",
    key=chart_key,
    on_select="rerun",
    selection_mode="points",
)


def _extract_selected_labels(event: object) -> list[tuple[str, str]]:
    """Pull (x_label, y_label) pairs out of Streamlit's PlotlyState.

    PlotlyState supports both ``event["selection"]`` and ``event.selection``;
    ``isinstance(event, dict)`` is False on streamlit>=1.30, so we duck-type
    each access. The overlay scatter's x/y values are the axis labels, so we
    map them straight back to (label, label).
    """
    if event is None:
        return []
    sel: object = None
    try:
        sel = event["selection"]  # type: ignore[index]
    except (TypeError, KeyError, IndexError):
        sel = None
    if sel is None:
        sel = getattr(event, "selection", None)
    if sel is None:
        return []
    pts: object = None
    try:
        pts = sel["points"]  # type: ignore[index]
    except (TypeError, KeyError, IndexError):
        pts = None
    if pts is None:
        pts = getattr(sel, "points", None)
    if not pts:
        return []
    out: list[tuple[str, str]] = []
    iterable_pts = list(pts)  # type: ignore[call-overload]
    for p in iterable_pts:
        if isinstance(p, dict):
            x_val, y_val = p.get("x"), p.get("y")
        else:
            x_val, y_val = getattr(p, "x", None), getattr(p, "y", None)
        if isinstance(x_val, str) and isinstance(y_val, str):
            out.append((x_val, y_val))
    return out


x_label_to_id = dict(zip(df["x_label"], df["x_term_id"], strict=False))
y_label_to_id = dict(zip(df["y_label"], df["y_term_id"], strict=False))

# Surface the heatmap's Top N x / y terms with popovers so users can read
# ontology metadata + sample counts without first picking a cell.
with st.expander("Top axis terms (ontology info)", expanded=False):
    col_x_terms, col_y_terms = st.columns(2)
    x_term_label = dict(
        zip(df["x_term_id"], df["x_label"], strict=False)
    )
    y_term_label = dict(
        zip(df["y_term_id"], df["y_label"], strict=False)
    )
    with col_x_terms:
        st.markdown(f"**X — {x_field}**")
        for tid, lbl in x_term_label.items():
            render_term_popover(con, x_field, tid, lbl, filters)
    with col_y_terms:
        st.markdown(f"**Y — {y_field}**")
        for tid, lbl in y_term_label.items():
            render_term_popover(con, y_field, tid, lbl, filters)

selected_labels = _extract_selected_labels(chart_event)
# Keep only selections that line up with a known axis label (defensive against
# overlay scatter points that drifted off the heatmap grid).
selected_labels = [
    (x, y) for x, y in selected_labels
    if x in x_label_to_id and y in y_label_to_id
]

# Switching axes invalidates previously picked cells — drop them so the table
# only ever holds picks that are meaningful for the current axes.
axis_key = (x_field, y_field)
if st.session_state.get("gap_axis_key") != axis_key:
    st.session_state["gap_selected_cells"] = []
    st.session_state["gap_axis_key"] = axis_key

cells: list[dict[str, str]] = st.session_state.setdefault(
    "gap_selected_cells", []
)
existing_keys = {(c["x_label"], c["y_label"]) for c in cells}
for x_lbl, y_lbl in selected_labels:
    if (x_lbl, y_lbl) in existing_keys:
        continue
    cells.append(
        {
            "x_label": x_lbl,
            "x_term_id": x_label_to_id[x_lbl],
            "y_label": y_lbl,
            "y_term_id": y_label_to_id[y_lbl],
        }
    )
    existing_keys.add((x_lbl, y_lbl))

st.subheader("Send selection to Cohort")
st.markdown(
    "1. **Click a cell** on the heatmap to add it to the table below. "
    "Click again to pick more — they accumulate as the cohort.\n"
    "2. Press **Open in Cohort →** to land in the drill-down with TSV "
    "download and DDBJ Search links."
)

if cells:
    rows = []
    for c in cells:
        sample_count = (
            int(pivot_counts.loc[c["y_label"], c["x_label"]])
            if c["y_label"] in pivot_counts.index
            and c["x_label"] in pivot_counts.columns
            else None
        )
        chip_atlas_count = (
            int(overlay_counts.loc[c["y_label"], c["x_label"]])
            if c["y_label"] in overlay_counts.index
            and c["x_label"] in overlay_counts.columns
            else None
        )
        rows.append(
            {
                x_field: c["x_label"],
                f"{x_field} term_id": c["x_term_id"],
                y_field: c["y_label"],
                f"{y_field} term_id": c["y_term_id"],
                "BioSample": sample_count,
                "of which ChIP-Atlas": chip_atlas_count,
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # Per-cell-term popover row: collapse selected cells into the unique
    # (field, term_id) terms involved, so the user can drill into ontology
    # info for each side without re-clicking the heatmap.
    selected_terms: dict[tuple[str, str], str] = {}
    for c in cells:
        selected_terms.setdefault((x_field, c["x_term_id"]), c["x_label"])
        selected_terms.setdefault((y_field, c["y_term_id"]), c["y_label"])
    st.caption("Term info for the selected cells:")
    for (field_name, term_id), lbl in selected_terms.items():
        render_term_popover(con, field_name, term_id, lbl, filters)
else:
    st.caption(
        "No cells picked yet. Click a cell on the heatmap to add it here."
    )

button_col, clear_col = st.columns([1, 1])
with button_col:
    if st.button(
        "Open in Cohort →",
        type="primary",
        width="stretch",
        key="gap_open_cohort",
        disabled=not cells,
    ):
        st.session_state["cohort_filters"] = filters
        st.session_state["cohort_facts_cells"] = [
            [(x_field, c["x_term_id"]), (y_field, c["y_term_id"])]
            for c in cells
        ]
        st.session_state.pop("cohort_facts_terms", None)
        if len(cells) == 1:
            c = cells[0]
            st.session_state["cohort_label"] = (
                f"{x_field}={c['x_label']} × {y_field}={c['y_label']}"
            )
        else:
            st.session_state["cohort_label"] = (
                f"{len(cells)} cells on {x_field} × {y_field}"
            )
        st.session_state.pop("cohort_seeded", None)
        st.switch_page("pages/02_Cohort.py")
with clear_col:
    if st.button(
        "Clear selection",
        width="stretch",
        key="gap_clear_selection",
        disabled=not cells,
    ):
        st.session_state["gap_selected_cells"] = []
        st.session_state["gap_heatmap_clear_nonce"] = clear_nonce + 1
        st.rerun()
