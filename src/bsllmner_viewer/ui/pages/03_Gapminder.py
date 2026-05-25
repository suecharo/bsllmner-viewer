from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    SampleFilters,
    bubble_dataset,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters

st.set_page_config(page_title="Gapminder — bsllmner-viewer", layout="wide")

st.title("Gapminder bubble")
st.caption(
    "Animated bubble of (submission_year × top ontology terms × organism). "
    "Bubble size = BioSample count, color = organism."
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


@st.cache_data(show_spinner="aggregating…")
def _bubble(
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
    return bubble_dataset(conn(), field_name, f, top_n)


df = _bubble(
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

# Each frame = one year. X axis = ontology term (label). Y axis = sample count.
df_plot = df.copy()
df_plot["label"] = df_plot["label"].fillna(df_plot["term_id"])
df_plot["submission_year"] = df_plot["submission_year"].astype(int)
df_plot = df_plot.sort_values(["submission_year", "label"])

fig = px.scatter(
    df_plot,
    x="label",
    y="sample_count",
    size="sample_count",
    color="organism_normalized",
    animation_frame="submission_year",
    animation_group="term_id",
    hover_data={
        "term_id": True,
        "chip_atlas_count": True,
        "submission_year": False,
        "sample_count": True,
    },
    size_max=60,
    labels={
        "label": field_name,
        "sample_count": "BioSample count",
        "organism_normalized": "Organism",
    },
)
fig.update_layout(
    height=600,
    xaxis={"categoryorder": "total descending"},
)

st.plotly_chart(fig, use_container_width=True)
st.caption(
    f"{len(df_plot)} (year × term × organism) cells across "
    f"{df_plot['submission_year'].nunique()} years."
)
