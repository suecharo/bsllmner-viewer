from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    FIELD_TO_ONTOLOGY,
    VALID_FIELDS,
    SampleFilters,
    bubble_dataset,
    bubble_dataset_fast,
    can_roll_up,
    concentration_over_time,
    cumulative_bubble_dataset,
    cumulative_diversity,
    has_dashboard_aggregates,
    momentum_dataset,
    term_hierarchy_breakdown,
)
from bsllmner_viewer.lib.ontology import term_summaries
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


def _depth_picker(field: str, key: str) -> int | None:
    """Mirror of Gap Discovery's depth picker.

    Hides itself for fields whose ontology has no usable hierarchy
    (Cellosaurus / NCBI Gene) — depth roll-up is a no-op there.
    """
    if not can_roll_up(field):
        st.caption(
            f"`{field}` rolls up against no usable hierarchy "
            "(Cellosaurus / NCBI Gene) — depth picker disabled."
        )
        return None
    source = FIELD_TO_ONTOLOGY[field]
    choices = ["leaf", "0", "1", "2", "3", "4", "5"]
    chosen = st.selectbox(
        f"Roll-up depth ({source})",
        options=choices,
        index=0,
        key=key,
        help=(
            "Roll each leaf term up to its ancestor at the chosen depth. "
            "`leaf` keeps the original term."
        ),
    )
    return None if chosen == "leaf" else int(chosen)


roll_up_depth = _depth_picker(field_name, "gapminder_depth_picker")

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
    sequence_type: tuple[str, ...],
    roll_up_depth: int | None,
) -> pd.DataFrame:
    f = SampleFilters(
        organism_normalized=organism,
        source_system=source,
        submission_year_min=year_min,
        submission_year_max=year_max,
        sequence_type=sequence_type,
    )
    c = conn()
    # fast path: agg_field_term_dims (small parquet) を読む。roll_up_depth が
    # 指定されている場合は ontology rollup が必要なので live を呼ぶ。
    use_fast = roll_up_depth is None and has_dashboard_aggregates(c)
    if mode == "Cumulative":
        if use_fast:
            base = bubble_dataset_fast(c, field_name, f, top_n)
            df = _cumulate(base)
        else:
            df = cumulative_bubble_dataset(
                c, field_name, f, top_n, roll_up_depth=roll_up_depth
            )
        df["count"] = df["sample_count_cum"]
        df["secondary"] = df["secondary_count_cum"]
    else:
        if use_fast:
            df = bubble_dataset_fast(c, field_name, f, top_n)
        else:
            df = bubble_dataset(
                c, field_name, f, top_n, roll_up_depth=roll_up_depth
            )
        df["count"] = df["sample_count"]
        df["secondary"] = df["secondary_count"]
    return df


def _cumulate(df: pd.DataFrame) -> pd.DataFrame:
    """``bubble_dataset_fast`` の戻り (年毎) を ``cumulative_bubble_dataset`` 形式
    (累積) に変換する純関数。
    """
    cum_cols = ["sample_count_cum", "secondary_count_cum"]
    if df.empty:
        for col in cum_cols:
            df[col] = pd.Series(dtype="int64")
        return df
    df = df.copy()
    df["submission_year"] = df["submission_year"].astype(int)
    years = sorted(df["submission_year"].unique())
    full_years = list(range(min(years), max(years) + 1))
    term_labels = (
        df.drop_duplicates("term_id").set_index("term_id")["label"].to_dict()
    )
    parts: list[pd.DataFrame] = []
    for (term_id, org), g in df.groupby(
        ["term_id", "organism_normalized"], sort=False
    ):
        sub = (
            g[["submission_year", "sample_count", "secondary_count"]]
            .set_index("submission_year")
            .reindex(full_years, fill_value=0)
        )
        sub["term_id"] = term_id
        sub["organism_normalized"] = org
        sub["label"] = term_labels.get(term_id, term_id)
        sub["sample_count_cum"] = sub["sample_count"].cumsum()
        sub["secondary_count_cum"] = sub["secondary_count"].cumsum()
        parts.append(sub.reset_index())
    return pd.concat(parts, ignore_index=True)


df = _load(
    mode,
    field_name,
    top_n,
    tuple(filters.organism_normalized),
    tuple(filters.source_system),
    filters.submission_year_min,
    filters.submission_year_max,
    tuple(filters.sequence_type),
    roll_up_depth,
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
    f"{mode}|{field_name}|{top_n}|{roll_up_depth}|{y_log}|{size_log}|"
    f"{tuple(filters.organism_normalized)}|{tuple(filters.source_system)}|"
    f"{tuple(filters.sequence_type)}|"
    f"{filters.submission_year_min}|{filters.submission_year_max}"
)

count_axis_title = (
    "BioSample count (cumulative)" if mode == "Cumulative"
    else "BioSample count (per-year)"
)
secondary_axis_title = (
    "Overlay count (cumulative)" if mode == "Cumulative"
    else "Overlay count (per-year)"
)

(
    tab_bubble,
    tab_line,
    tab_race,
    tab_heatmap,
    tab_comp,
    tab_slope,
    tab_momentum,
    tab_diversity,
    tab_concentration,
    tab_treemap,
) = st.tabs(
    [
        "Bubble",
        "Trajectory",
        "Rank race",
        "Heatmap",
        "Composition",
        "Slope",
        "Momentum",
        "Diversity",
        "Concentration",
        "Treemap",
    ]
)

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
                "secondary": True,
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
                "secondary": secondary_axis_title,
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
            "secondary": True,
            "organism_normalized": True,
        },
        markers=True,
        log_y=y_log,
        labels={
            "submission_year": "Submission year",
            "count": count_axis_title,
            "secondary": secondary_axis_title,
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

# Rank race / Heatmap / Composition operate at (year × term) granularity —
# organism is summed out so each term has a single trace. Done in pandas so
# every toggle (mode / log) still triggers a fresh render.
agg_df = (
    df.groupby(["submission_year", "term_id", "label"], as_index=False)
    .agg(count=("count", "sum"), secondary=("secondary", "sum"))
)
agg_df["submission_year"] = agg_df["submission_year"].astype(int)
# Order terms by their overall maximum count across the timeline — used as a
# stable y/category order for the rank race + heatmap + composition tabs.
overall_order = (
    agg_df.groupby("label")["count"]
    .max()
    .sort_values(ascending=False)
    .index.tolist()
)

with tab_race:
    if agg_df["count"].sum() == 0:
        st.info(
            "All counts are zero for the current selection. "
            "Switch *Aggregation* to Cumulative or relax the filters."
        )
    else:
        race_df = agg_df.copy()
        race_df["rank"] = race_df.groupby("submission_year")["count"].rank(
            ascending=False, method="first"
        )
        max_x = float(race_df["count"].max()) or 1.0
        fig_race = px.bar(
            race_df,
            x="count",
            y="label",
            orientation="h",
            color="label",
            animation_frame="submission_year",
            animation_group="label",
            hover_data={
                "term_id": True,
                "rank": True,
                "secondary": True,
                "label": False,
                "submission_year": False,
            },
            range_x=(
                [0.8, max_x * 1.4] if y_log else [0, max_x * 1.05]
            ),
            log_x=y_log,
            labels={
                "count": count_axis_title,
                "label": field_name,
                "secondary": secondary_axis_title,
            },
        )
        # Pin category order by overall importance so bars don't fully
        # reshuffle every frame — Plotly's animation can't sort per frame,
        # so a stable axis with shrinking/growing bars conveys the race best.
        fig_race.update_yaxes(
            categoryorder="array",
            categoryarray=list(reversed(overall_order)),
        )
        fig_race.update_layout(
            height=max(400, 28 * len(overall_order)),
            showlegend=False,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        if fig_race.layout.updatemenus:
            fig_race.layout.updatemenus[0]["buttons"][0]["args"][1]["frame"][
                "duration"
            ] = 800
            fig_race.layout.updatemenus[0]["buttons"][0]["args"][1][
                "transition"
            ]["duration"] = 400
        st.plotly_chart(
            fig_race, width="stretch", key=f"race_{chart_state_key}"
        )
        st.caption(
            f"Bars are ordered by overall {mode.lower()} count across the "
            "timeline; their length shrinks/grows per frame. Numeric rank "
            "shown in the hover tooltip."
        )

with tab_heatmap:
    if agg_df["count"].sum() == 0:
        st.info("All counts are zero for the current selection.")
    else:
        pivot = (
            agg_df.pivot_table(
                index="label",
                columns="submission_year",
                values="count",
                aggfunc="sum",
                fill_value=0,
            )
            .reindex(overall_order)
            .sort_index(axis=1)
        )
        # Log color compresses the dynamic range so 16K vs 100 still both
        # show variation; +1 avoids -inf for empty cells.
        z_values = np.log10(pivot.to_numpy() + 1) if y_log else pivot.to_numpy()
        color_label = (
            "log10(count + 1)" if y_log else count_axis_title
        )
        fig_heat = px.imshow(
            z_values,
            x=[int(c) for c in pivot.columns],
            y=list(pivot.index),
            aspect="auto",
            color_continuous_scale="Blues",
            labels={
                "x": "submission_year",
                "y": field_name,
                "color": color_label,
            },
        )
        fig_heat.update_layout(
            height=max(400, 28 * len(pivot.index)),
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(
            fig_heat, width="stretch", key=f"heatmap_{chart_state_key}"
        )
        st.caption(
            f"Static year × term heatmap. Color = "
            f"{'log10(count + 1)' if y_log else mode.lower() + ' count'}. "
            "Row order = overall importance descending."
        )

with tab_comp:
    if agg_df["count"].sum() == 0:
        st.info("All counts are zero for the current selection.")
    else:
        normalize = st.toggle(
            "100% stacked (share, not absolute)",
            value=False,
            key=f"comp_norm_{chart_state_key}",
            help=(
                "On: each year's bars sum to 100% (share). "
                "Off: stacked area shows absolute counts."
            ),
        )
        comp_df = agg_df.sort_values(["submission_year", "label"])
        fig_comp = px.area(
            comp_df,
            x="submission_year",
            y="count",
            color="label",
            groupnorm="percent" if normalize else None,
            category_orders={"label": overall_order},
            hover_data={"term_id": True, "secondary": True},
            labels={
                "submission_year": "Submission year",
                "count": (
                    "Share (%)" if normalize else count_axis_title
                ),
                "label": field_name,
                "secondary": secondary_axis_title,
            },
        )
        if normalize:
            fig_comp.update_yaxes(range=[0, 100], ticksuffix="%")
        elif y_log:
            # Log on a stacked area is misleading (areas don't compose under
            # log); only apply it on the absolute-mode linear stack when the
            # user explicitly opted in by leaving Y log on.
            fig_comp.update_yaxes(type="log")
        fig_comp.update_layout(
            height=600,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(
            fig_comp,
            width="stretch",
            key=f"comp_{chart_state_key}_{normalize}",
        )
        st.caption(
            "Composition view: how the top terms' share of total "
            "submissions evolves across years. "
            f"{'100% stacked.' if normalize else 'Absolute stacked area.'}"
        )

with tab_slope:
    # A5: 2-year slope graph. Picks two years (default min, max of the
    # current dataset) and draws one line per top-N term, exposing the slope
    # itself as the visual signal — steep up = breakout, steep down = decline.
    if agg_df["count"].sum() == 0:
        st.info("All counts are zero for the current selection.")
    else:
        available_years = sorted(agg_df["submission_year"].astype(int).unique())
        if len(available_years) < 2:
            st.info(
                "Slope graph needs at least 2 years of data; current dataset "
                f"has only {len(available_years)}."
            )
        else:
            col_y1, col_y2 = st.columns(2)
            with col_y1:
                year_start = st.selectbox(
                    "Year (start)",
                    options=available_years,
                    index=0,
                    key=f"slope_y1_{chart_state_key}",
                )
            with col_y2:
                year_end = st.selectbox(
                    "Year (end)",
                    options=available_years,
                    index=len(available_years) - 1,
                    key=f"slope_y2_{chart_state_key}",
                )
            if year_start == year_end:
                st.info("Start and end years must differ.")
            else:
                lo, hi = sorted((int(year_start), int(year_end)))
                slope_df = (
                    agg_df[agg_df["submission_year"].isin([lo, hi])]
                    .pivot_table(
                        index=["term_id", "label"],
                        columns="submission_year",
                        values="count",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    .reset_index()
                )
                slope_long = (
                    slope_df.melt(
                        id_vars=["term_id", "label"],
                        value_vars=[lo, hi],
                        var_name="submission_year",
                        value_name="count",
                    )
                    .assign(submission_year=lambda d: d["submission_year"].astype(int))
                )
                slope_df["delta"] = slope_df[hi] - slope_df[lo]
                # Annotate delta in hover so the slope numbers are explicit.
                slope_long = slope_long.merge(
                    slope_df[["term_id", "delta"]], on="term_id"
                )
                max_y = float(slope_long["count"].max()) or 1.0
                fig_slope = px.line(
                    slope_long,
                    x="submission_year",
                    y="count",
                    color="label",
                    markers=True,
                    hover_data={"term_id": True, "delta": True},
                    log_y=y_log,
                    range_y=(
                        [0.8, max_y * 1.4] if y_log else [0, max_y * 1.1]
                    ),
                    labels={
                        "submission_year": "Year",
                        "count": count_axis_title,
                        "label": field_name,
                        "delta": f"Δ ({hi} − {lo})",
                    },
                )
                fig_slope.update_xaxes(
                    tickmode="array",
                    tickvals=[lo, hi],
                    ticktext=[str(lo), str(hi)],
                )
                fig_slope.update_layout(
                    height=max(400, 24 * len(slope_df)),
                    modebar={"remove": ["fullscreen", "togglefullscreen"]},
                )
                st.plotly_chart(
                    fig_slope,
                    width="stretch",
                    key=f"slope_{chart_state_key}_{lo}_{hi}",
                )
                st.caption(
                    f"Slope = count change between {lo} and {hi}. "
                    "Steep up/down = recent breakout / decline."
                )

with tab_momentum:
    # A6: per-term momentum scatter. X = absolute level, Y = year-over-year
    # delta, size = cumulative volume. The latest-year slice exposes "right-
    # upper hot zone" terms (high count + still growing) vs "high count but
    # decaying" (right-lower) at a glance.
    @st.cache_data(show_spinner="computing momentum…")
    def _load_momentum(
        field_name: str,
        top_n: int,
        organism: tuple[str, ...],
        source: tuple[str, ...],
        year_min: int | None,
        year_max: int | None,
        sequence_type: tuple[str, ...],
        roll_up_depth: int | None,
    ) -> pd.DataFrame:
        f = SampleFilters(
            organism_normalized=organism,
            source_system=source,
            submission_year_min=year_min,
            submission_year_max=year_max,
            sequence_type=sequence_type,
        )
        return momentum_dataset(
            conn(), field_name, f, top_n=top_n, roll_up_depth=roll_up_depth
        )

    mom_df = _load_momentum(
        field_name,
        top_n,
        tuple(filters.organism_normalized),
        tuple(filters.source_system),
        filters.submission_year_min,
        filters.submission_year_max,
        tuple(filters.sequence_type),
        roll_up_depth,
    )
    if mom_df.empty:
        st.info("No momentum data for the current filters.")
    else:
        years_mom = sorted(mom_df["submission_year"].astype(int).unique())
        latest = int(years_mom[-1])
        focus_year = st.selectbox(
            "Year (snapshot)",
            options=years_mom,
            index=len(years_mom) - 1,
            key=f"momentum_year_{chart_state_key}",
            help=(
                "Each dot is a term in this year: X = level, Y = year-over-"
                "year delta. Right-up = growing, right-down = receding."
            ),
        )
        snap = mom_df[mom_df["submission_year"].astype(int) == int(focus_year)].copy()
        # Drop fully-zero rows (no level AND no movement) — they crowd the
        # origin without telling us anything.
        snap = snap[~((snap["count_abs"] == 0) & (snap["count_delta"] == 0))]
        if snap.empty:
            st.info(
                f"No term has any activity in {focus_year}. "
                "Try a later year."
            )
        else:
            if size_log:
                snap["bubble_size"] = np.log10(
                    snap["count_cum"].clip(lower=1)
                ) + 1
            else:
                snap["bubble_size"] = snap["count_cum"].clip(lower=0)
            fig_mom = px.scatter(
                snap,
                x="count_abs",
                y="count_delta",
                size="bubble_size",
                size_max=32,
                color="label",
                hover_name="label",
                hover_data={
                    "term_id": True,
                    "count_abs": True,
                    "count_delta": True,
                    "count_cum": True,
                    "bubble_size": False,
                    "label": False,
                },
                log_x=y_log,
                labels={
                    "count_abs": f"{focus_year} count (level)",
                    "count_delta": (
                        f"Year-over-year Δ ({focus_year} − {int(focus_year) - 1})"
                    ),
                    "count_cum": "Cumulative",
                    "label": field_name,
                },
            )
            # A reference line at delta=0 separates growing from receding.
            fig_mom.add_hline(
                y=0, line_dash="dot", line_color="grey", opacity=0.7
            )
            fig_mom.update_layout(
                height=600,
                modebar={"remove": ["fullscreen", "togglefullscreen"]},
            )
            st.plotly_chart(
                fig_mom,
                width="stretch",
                key=f"momentum_{chart_state_key}_{focus_year}",
            )
            st.caption(
                f"Momentum snapshot at {focus_year}. Bubble size = "
                f"{'log10(cumulative + 1)' if size_log else 'cumulative'} "
                "samples."
            )

with tab_diversity:
    # A8: cumulative unique-term curve. Plateau = ontology saturation
    # (research re-uses the same set), steady climb = new terms keep
    # appearing.
    div_group = st.radio(
        "Split by",
        options=["Overall", "Organism", "Source system"],
        index=0,
        horizontal=True,
        key=f"div_group_{chart_state_key}",
    )
    group_by_arg: str | None = {
        "Overall": None,
        "Organism": "organism_normalized",
        "Source system": "source_system",
    }[div_group]

    @st.cache_data(show_spinner="counting unique terms…")
    def _load_diversity(
        field_name: str,
        group_by: str | None,
        organism: tuple[str, ...],
        source: tuple[str, ...],
        year_min: int | None,
        year_max: int | None,
        sequence_type: tuple[str, ...],
        roll_up_depth: int | None,
    ) -> pd.DataFrame:
        f = SampleFilters(
            organism_normalized=organism,
            source_system=source,
            submission_year_min=year_min,
            submission_year_max=year_max,
            sequence_type=sequence_type,
        )
        return cumulative_diversity(
            conn(), field_name, f, group_by=group_by, roll_up_depth=roll_up_depth
        )

    div_df = _load_diversity(
        field_name,
        group_by_arg,
        tuple(filters.organism_normalized),
        tuple(filters.source_system),
        filters.submission_year_min,
        filters.submission_year_max,
        tuple(filters.sequence_type),
        roll_up_depth,
    )
    if div_df.empty:
        st.info("No diversity data for the current filters.")
    else:
        fig_div = px.line(
            div_df,
            x="submission_year",
            y="cum_unique_terms",
            color="group_value",
            markers=True,
            hover_data={"unique_terms": True},
            log_y=y_log,
            labels={
                "submission_year": "Year",
                "cum_unique_terms": "Cumulative unique terms",
                "unique_terms": "New that year",
                "group_value": "Group",
            },
        )
        fig_div.update_layout(
            height=520,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(
            fig_div,
            width="stretch",
            key=f"div_{chart_state_key}_{div_group}",
        )
        st.caption(
            "Cumulative unique terms over time (running union). "
            "Plateau = ontology saturation, steady climb = new terms still "
            "arriving."
        )

with tab_concentration:
    # A9: per-year Gini + Shannon (max-normalized) on the per-term sample
    # distribution. High Gini / low Shannon = research concentrates on a
    # few hot terms; low Gini / high Shannon = work is spread out.
    @st.cache_data(show_spinner="computing concentration…")
    def _load_concentration(
        field_name: str,
        organism: tuple[str, ...],
        source: tuple[str, ...],
        year_min: int | None,
        year_max: int | None,
        sequence_type: tuple[str, ...],
        roll_up_depth: int | None,
    ) -> pd.DataFrame:
        f = SampleFilters(
            organism_normalized=organism,
            source_system=source,
            submission_year_min=year_min,
            submission_year_max=year_max,
            sequence_type=sequence_type,
        )
        return concentration_over_time(
            conn(), field_name, f, roll_up_depth=roll_up_depth
        )

    conc_df = _load_concentration(
        field_name,
        tuple(filters.organism_normalized),
        tuple(filters.source_system),
        filters.submission_year_min,
        filters.submission_year_max,
        tuple(filters.sequence_type),
        roll_up_depth,
    )
    if conc_df.empty:
        st.info("No concentration data for the current filters.")
    else:
        conc_long = conc_df.melt(
            id_vars=["submission_year", "n_terms", "total_samples"],
            value_vars=["gini", "shannon"],
            var_name="metric",
            value_name="value",
        )
        fig_conc = px.line(
            conc_long,
            x="submission_year",
            y="value",
            color="metric",
            markers=True,
            hover_data={"n_terms": True, "total_samples": True},
            labels={
                "submission_year": "Year",
                "value": "Concentration / Entropy",
                "metric": "Metric",
                "n_terms": "Terms that year",
                "total_samples": "Samples that year",
            },
        )
        fig_conc.update_yaxes(range=[0, 1])
        fig_conc.update_layout(
            height=520,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(
            fig_conc,
            width="stretch",
            key=f"conc_{chart_state_key}",
        )
        st.caption(
            "Gini → 1 = concentrated; Gini → 0 = uniform. "
            "Shannon (max-normalized) is the inverse signal: 0 = single "
            "dominant term, 1 = perfectly uniform."
        )

with tab_treemap:
    # A11: ontology subtree treemap, animated by year. Roll-up-capable
    # fields only — the rest get a caption explaining why.
    if not can_roll_up(field_name):
        st.info(
            f"Treemap needs a usable ontology hierarchy. `{field_name}` "
            "(Cellosaurus / NCBI Gene) has none — pick disease / cell_type "
            "/ tissue / drug instead."
        )
    else:
        tree_depth = st.slider(
            "Treemap max depth (from ontology root)",
            min_value=1,
            max_value=4,
            value=2,
            key=f"tree_depth_{chart_state_key}",
            help=(
                "Higher = more cells, finer hierarchy. Plotly treemap can "
                "stall above ~500 boxes; keep it small."
            ),
        )

        @st.cache_data(show_spinner="building hierarchy…")
        def _load_hierarchy(
            field_name: str,
            depth: int,
            organism: tuple[str, ...],
            source: tuple[str, ...],
            year_min: int | None,
            year_max: int | None,
            sequence_type: tuple[str, ...],
        ) -> pd.DataFrame:
            f = SampleFilters(
                organism_normalized=organism,
                source_system=source,
                submission_year_min=year_min,
                submission_year_max=year_max,
                sequence_type=sequence_type,
            )
            return term_hierarchy_breakdown(
                conn(),
                field_name,
                f,
                max_depth=depth,
                by_year=True,
            )

        hier_df = _load_hierarchy(
            field_name,
            tree_depth,
            tuple(filters.organism_normalized),
            tuple(filters.source_system),
            filters.submission_year_min,
            filters.submission_year_max,
            tuple(filters.sequence_type),
        )
        if hier_df.empty:
            st.info(
                "No subtree terms have samples under the current filters."
            )
        else:
            hier_df = hier_df.copy()
            hier_df["secondary_ratio"] = np.where(
                hier_df["sample_count"] > 0,
                hier_df["secondary_count"] / hier_df["sample_count"],
                0.0,
            )
            # Plotly treemap needs every parent_term_id to appear as a
            # term_id too (or be empty). Inject synthetic root rows for any
            # parent that's missing from the data so the tree closes.
            missing_parents = set(hier_df["parent_term_id"]) - set(
                hier_df["term_id"]
            ) - {""}
            if missing_parents:
                pad_rows = [
                    {
                        "submission_year": year,
                        "term_id": p,
                        "parent_term_id": "",
                        "label": p,
                        "depth": 0,
                        "sample_count": 0,
                        "secondary_count": 0,
                        "secondary_ratio": 0.0,
                    }
                    for year in sorted(
                        hier_df["submission_year"].astype(int).unique()
                    )
                    for p in missing_parents
                ]
                hier_df = pd.concat(
                    [hier_df, pd.DataFrame(pad_rows)], ignore_index=True
                )
            # plotly.express.treemap does NOT support animation_frame (it
            # would render once and freeze on frame 1). Use a year picker
            # to switch the static snapshot instead.
            tree_years = sorted(
                hier_df["submission_year"].astype(int).unique().tolist()
            )
            tree_year = st.selectbox(
                "Year (snapshot)",
                options=tree_years,
                index=len(tree_years) - 1,
                key=f"tree_year_{chart_state_key}",
                help=(
                    "Treemap shows the hierarchy at this year only. "
                    "Plotly Express treemap doesn't animate, so step "
                    "through years to see growth."
                ),
            )
            year_slice = hier_df[
                hier_df["submission_year"].astype(int) == int(tree_year)
            ]
            if year_slice.empty:
                st.info(f"No subtree data for {tree_year}.")
            else:
                fig_tree = px.treemap(
                    year_slice,
                    ids="term_id",
                    parents="parent_term_id",
                    names="label",
                    values="sample_count",
                    color="secondary_ratio",
                    color_continuous_scale="RdYlGn",
                    range_color=(0.0, 1.0),
                    hover_data={
                        "term_id": True,
                        "depth": True,
                        "secondary_count": True,
                        "secondary_ratio": ":.2f",
                    },
                    labels={
                        "secondary_ratio": "Overlay %",
                        "secondary_count": "Overlay samples",
                    },
                )
                fig_tree.update_layout(
                    height=700,
                    modebar={"remove": ["fullscreen", "togglefullscreen"]},
                )
                st.plotly_chart(
                    fig_tree,
                    width="stretch",
                    key=f"tree_{chart_state_key}_{tree_depth}_{tree_year}",
                )
                st.caption(
                    f"Ontology subtree at {tree_year}. Size = BioSample "
                    "count; color = overlay coverage ratio (red = "
                    "uncovered, green = covered)."
                )

# Show the Top N (term_id, label) selection from the underlying dataset and
# expose each one as a term-info popover. The bubble chart itself can't be
# wired to a click handler, so this section is the navigation hand-off.
st.subheader(f"Top terms for `{field_name}`")
term_label_map = (
    df.drop_duplicates("term_id").set_index("term_id")["label"].to_dict()
)
top_term_summaries = term_summaries(con, list(term_label_map.keys()))
for term_id, lbl in term_label_map.items():
    render_term_popover(
        con, field_name, term_id, lbl, filters,
        summary=top_term_summaries.get(term_id),
    )
