from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    FIELD_TO_ONTOLOGY,
    VALID_FIELDS,
    SampleFilters,
    can_roll_up,
    field_to_field_flow,
    gap_heatmap_pivot,
    term_hierarchy_breakdown,
)
from bsllmner_viewer.lib.ontology import term_summaries
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters
from bsllmner_viewer.ui._term_popover import render_term_popover

st.set_page_config(page_title="Gap Discovery — bsllmner-viewer", layout="wide")

st.title("Gap Discovery")
st.caption(
    "Cross-field exploration of where BioSamples cluster and where they "
    "don't. Three views share the field / depth pickers and the sidebar "
    "filter: heatmap (count grid), sunburst (ontology subtree), sankey "
    "(field-to-field flow)."
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

tab_heatmap, tab_sunburst, tab_sankey = st.tabs(["Heatmap", "Sunburst", "Sankey"])

with tab_heatmap:
    DISPLAY_MODES = (
        "BioSample count",
        "ChIP-Atlas count",
        "ChIP-Atlas ratio",
        "Gap only (sample > 0, ChIP-Atlas = 0)",
    )
    display_mode = st.radio(
        "Color values",
        options=DISPLAY_MODES,
        index=0,
        horizontal=True,
        help=(
            "Switch what the heatmap color represents. The underlying cohort is "
            "unchanged — only the color value and scale are remapped."
        ),
        key="gap_display_mode",
    )

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

    if display_mode == "BioSample count":
        z_matrix = pivot_counts.astype(float)
        color_scale = "Blues"
        color_label = "BioSample count"
        zmin: float | None = 0.0
        zmax: float | None = None
    elif display_mode == "ChIP-Atlas count":
        z_matrix = overlay_counts.astype(float)
        color_scale = "Blues"
        color_label = "ChIP-Atlas count"
        zmin = 0.0
        zmax = None
    elif display_mode == "ChIP-Atlas ratio":
        safe_sample = pivot_counts.astype(float).replace(0, np.nan)
        z_matrix = overlay_counts.astype(float) / safe_sample
        color_scale = "RdYlGn"
        color_label = "ChIP-Atlas / BioSample"
        zmin = 0.0
        zmax = 1.0
    else:  # Gap only
        has_gap = (pivot_counts > 0) & (overlay_counts == 0)
        masked = pivot_counts.astype(float).where(has_gap, np.nan)
        z_matrix = masked
        color_scale = "Reds"
        color_label = "BioSample count (gap)"
        zmin = 0.0
        zmax = None

    fig = px.imshow(
        z_matrix,
        aspect="auto",
        color_continuous_scale=color_scale,
        zmin=zmin,
        zmax=zmax,
        labels={"x": x_field, "y": y_field, "color": color_label},
    )

    # Overlay one transparent square per heatmap cell so a single click maps
    # back to (x_label, y_label) — Plotly's Heatmap trace alone doesn't
    # surface point selection events through Streamlit's selection API.
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

    clear_nonce = st.session_state.get("gap_heatmap_clear_nonce", 0)
    chart_key = (
        f"gap_heatmap__{x_field}__{y_field}__{display_mode}__{clear_nonce}"
    )
    chart_event: object = st.plotly_chart(
        fig,
        width="stretch",
        key=chart_key,
        on_select="rerun",
        selection_mode="points",
    )

    def _extract_selected_labels(event: object) -> list[tuple[str, str]]:
        """Pull (x_label, y_label) pairs out of Streamlit's PlotlyState.

        PlotlyState supports both ``event["selection"]`` and
        ``event.selection``; ``isinstance(event, dict)`` is False on
        streamlit>=1.30, so we duck-type each access.
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

    if st.toggle(
        "Show Top axis terms (ontology info)",
        value=False,
        key="gap_show_axis_terms",
        help=(
            "Renders one ontology info popover per top axis term. Off by "
            "default because building N popovers is the dominant cost of a "
            "heatmap-click rerun."
        ),
    ):
        col_x_terms, col_y_terms = st.columns(2)
        x_term_label = dict(
            zip(df["x_term_id"], df["x_label"], strict=False)
        )
        y_term_label = dict(
            zip(df["y_term_id"], df["y_label"], strict=False)
        )
        axis_summaries = term_summaries(
            con, list({*x_term_label.keys(), *y_term_label.keys()})
        )
        with col_x_terms:
            st.markdown(f"**X — {x_field}**")
            for tid, lbl in x_term_label.items():
                render_term_popover(
                    con, x_field, tid, lbl, filters,
                    summary=axis_summaries.get(tid),
                )
        with col_y_terms:
            st.markdown(f"**Y — {y_field}**")
            for tid, lbl in y_term_label.items():
                render_term_popover(
                    con, y_field, tid, lbl, filters,
                    summary=axis_summaries.get(tid),
                )

    selected_labels = _extract_selected_labels(chart_event)
    selected_labels = [
        (x, y) for x, y in selected_labels
        if x in x_label_to_id and y in y_label_to_id
    ]

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

        selected_terms: dict[tuple[str, str], str] = {}
        for c in cells:
            selected_terms.setdefault((x_field, c["x_term_id"]), c["x_label"])
            selected_terms.setdefault((y_field, c["y_term_id"]), c["y_label"])
        st.caption("Term info for the selected cells:")
        cell_summaries = term_summaries(
            con, list({tid for _, tid in selected_terms})
        )
        for (field_name, term_id), lbl in selected_terms.items():
            render_term_popover(
                con, field_name, term_id, lbl, filters,
                summary=cell_summaries.get(term_id),
            )
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


with tab_sunburst:
    # B3: ontology subtree drill-in. The X-axis field's primary ontology is
    # the natural anchor (the Y field's hierarchy could be shown alongside
    # but that doubles the cognitive load — keep it single-field for
    # clarity, the user can swap X if they want the other side).
    if not can_roll_up(x_field):
        st.info(
            f"Sunburst needs a usable ontology hierarchy. The X field "
            f"`{x_field}` (Cellosaurus / NCBI Gene) has none — pick "
            "disease / cell_type / tissue / drug as X instead."
        )
    else:
        sb_depth = st.slider(
            "Sunburst max depth (from ontology root)",
            min_value=1,
            max_value=5,
            value=3,
            key="sunburst_depth",
            help=(
                "Higher = deeper drill. Plotly sunburst slows around "
                "~500 wedges; keep moderate."
            ),
        )

        @st.cache_data(show_spinner="building hierarchy…")
        def _load_sunburst(
            field: str,
            depth: int,
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
            return term_hierarchy_breakdown(
                conn(), field, f, max_depth=depth, by_year=False
            )

        sb_df = _load_sunburst(
            x_field,
            sb_depth,
            tuple(filters.organism_normalized),
            tuple(filters.source_system),
            filters.submission_year_min,
            filters.submission_year_max,
            filters.in_chip_atlas,
        )
        if sb_df.empty:
            st.info(
                "No subtree terms have samples under the current filters."
            )
        else:
            sb_df = sb_df.copy()
            sb_df["chip_atlas_ratio"] = np.where(
                sb_df["sample_count"] > 0,
                sb_df["chip_atlas_count"] / sb_df["sample_count"],
                0.0,
            )
            # Pad missing parents so Plotly can close the tree.
            missing = (
                set(sb_df["parent_term_id"]) - set(sb_df["term_id"]) - {""}
            )
            if missing:
                pad = pd.DataFrame(
                    [
                        {
                            "term_id": p,
                            "parent_term_id": "",
                            "label": p,
                            "depth": 0,
                            "sample_count": 0,
                            "chip_atlas_count": 0,
                            "chip_atlas_ratio": 0.0,
                        }
                        for p in missing
                    ]
                )
                sb_df = pd.concat([sb_df, pad], ignore_index=True)
            fig_sb = px.sunburst(
                sb_df,
                ids="term_id",
                parents="parent_term_id",
                names="label",
                values="sample_count",
                color="chip_atlas_ratio",
                color_continuous_scale="RdYlGn",
                range_color=(0.0, 1.0),
                hover_data={
                    "term_id": True,
                    "depth": True,
                    "chip_atlas_count": True,
                    "chip_atlas_ratio": ":.2f",
                },
                labels={
                    "chip_atlas_ratio": "ChIP-Atlas %",
                    "chip_atlas_count": "ChIP-Atlas samples",
                },
            )
            fig_sb.update_layout(
                height=700,
                modebar={"remove": ["fullscreen", "togglefullscreen"]},
            )
            st.plotly_chart(
                fig_sb,
                width="stretch",
                key=f"sunburst__{x_field}__{sb_depth}",
            )
            st.caption(
                f"Ontology subtree for `{x_field}` "
                f"({FIELD_TO_ONTOLOGY[x_field]}). Size = BioSample count; "
                "color = ChIP-Atlas coverage ratio."
            )


with tab_sankey:
    # B4: field-to-field sample flow. Each link's width = sample count for
    # the (x_term, y_term) cell. Node names are namespaced with the field
    # name to avoid collisions when the same term_id appears on both sides
    # (rare but possible across some ontologies).
    @st.cache_data(show_spinner="computing flows…")
    def _load_flow(
        x_field: str,
        y_field: str,
        top_n_x: int,
        top_n_y: int,
        organism: tuple[str, ...],
        source: tuple[str, ...],
        year_min: int | None,
        year_max: int | None,
        in_chip_atlas: bool | None,
        x_depth: int | None,
        y_depth: int | None,
    ) -> pd.DataFrame:
        f = SampleFilters(
            organism_normalized=list(organism),
            source_system=list(source),
            submission_year_min=year_min,
            submission_year_max=year_max,
            in_chip_atlas=in_chip_atlas,
        )
        return field_to_field_flow(
            conn(),
            x_field,
            y_field,
            f,
            top_n_x=top_n_x,
            top_n_y=top_n_y,
            x_roll_up_depth=x_depth,
            y_roll_up_depth=y_depth,
        )

    sank_top = st.slider(
        "Top N per side",
        min_value=5,
        max_value=30,
        value=15,
        key="sankey_top",
        help=(
            "Plotly sankey crowds quickly above 40 nodes total. Each side "
            "contributes up to N nodes."
        ),
    )

    flow_df = _load_flow(
        x_field,
        y_field,
        sank_top,
        sank_top,
        tuple(filters.organism_normalized),
        tuple(filters.source_system),
        filters.submission_year_min,
        filters.submission_year_max,
        filters.in_chip_atlas,
        x_roll_up_depth,
        y_roll_up_depth,
    )
    if flow_df.empty:
        st.info(
            "No (x, y) flow rows have samples under the current filters."
        )
    else:
        # Namespaced node names so left and right sides never collide.
        x_nodes_ordered = (
            flow_df.groupby("x_label")["sample_count"]
            .sum()
            .sort_values(ascending=False)
            .index.tolist()
        )
        y_nodes_ordered = (
            flow_df.groupby("y_label")["sample_count"]
            .sum()
            .sort_values(ascending=False)
            .index.tolist()
        )
        node_names: list[str] = []
        node_colors: list[str] = []
        x_node_idx: dict[str, int] = {}
        y_node_idx: dict[str, int] = {}
        for lbl in x_nodes_ordered:
            x_node_idx[lbl] = len(node_names)
            node_names.append(f"{x_field}: {lbl}")
            node_colors.append("rgba(31,119,180,0.8)")  # left = blue
        for lbl in y_nodes_ordered:
            y_node_idx[lbl] = len(node_names)
            node_names.append(f"{y_field}: {lbl}")
            node_colors.append("rgba(255,127,14,0.8)")  # right = orange
        sources = [x_node_idx[r] for r in flow_df["x_label"]]
        targets = [y_node_idx[r] for r in flow_df["y_label"]]
        values = flow_df["sample_count"].astype(int).tolist()
        link_hover = [
            (
                f"{x_field}: {r['x_label']}<br>"
                f"{y_field}: {r['y_label']}<br>"
                f"BioSample: {int(r['sample_count'])}<br>"
                f"of which ChIP-Atlas: {int(r['chip_atlas_count'])}"
            )
            for _, r in flow_df.iterrows()
        ]

        fig_sank = go.Figure(
            data=[
                go.Sankey(
                    arrangement="snap",
                    node={
                        "label": node_names,
                        "color": node_colors,
                        "pad": 12,
                        "thickness": 18,
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
        fig_sank.update_layout(
            height=max(400, 22 * max(len(x_nodes_ordered), len(y_nodes_ordered))),
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(
            fig_sank,
            width="stretch",
            key=f"sankey__{x_field}__{y_field}__{sank_top}",
        )
        st.caption(
            f"Sample flow `{x_field}` (left, blue) → `{y_field}` (right, "
            "orange). Link width = BioSample count for the cell."
        )
