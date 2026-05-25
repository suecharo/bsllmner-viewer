from __future__ import annotations

from typing import cast

import pandas as pd
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    SampleFilters,
    cohort_count,
    cohort_samples,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters

st.set_page_config(page_title="Cohort — bsllmner-viewer", layout="wide")

st.title("Cohort drill-down")

con = conn()

filters_default = cast(
    SampleFilters | None, st.session_state.get("cohort_filters")
)
facts_terms = cast(
    list[tuple[str, str]] | None, st.session_state.get("cohort_facts_terms")
)
label = cast(str | None, st.session_state.get("cohort_label"))

if label:
    st.info(f"Cohort: {label}")
elif facts_terms:
    formatted = ", ".join(f"{f}={t}" for f, t in facts_terms)
    st.info(f"Cohort: {formatted}")
else:
    st.caption("No cohort selected. Use the Gap Discovery page or filter directly.")

# Sidebar filter widgets override the cohort filters carried via session_state
# whenever the user touches them. We seed the widgets from session_state defaults
# only on the first render of this page.
if filters_default is not None and "cohort_seeded" not in st.session_state:
    st.session_state["filter_organism"] = filters_default.organism_normalized
    st.session_state["filter_source"] = filters_default.source_system
    if filters_default.in_chip_atlas is True:
        st.session_state["filter_chip_atlas"] = "Only ChIP-Atlas"
    elif filters_default.in_chip_atlas is False:
        st.session_state["filter_chip_atlas"] = "Exclude ChIP-Atlas"
    st.session_state["cohort_seeded"] = True

filters = sidebar_filters(con)

total = cohort_count(con, filters, facts_terms=facts_terms)
st.metric("Total BioSamples", f"{total:,}")

if total == 0:
    st.warning("No samples match the current cohort.")
    st.stop()

LIMIT = 10000
df = cohort_samples(con, filters, facts_terms=facts_terms, limit=LIMIT)
if total > LIMIT:
    st.warning(
        f"Showing the first {LIMIT:,} of {total:,} samples (sorted by year desc). "
        "Refine filters to narrow down."
    )

st.dataframe(
    df,
    width="stretch",
    hide_index=True,
    column_config={
        "accession": st.column_config.TextColumn(width="small"),
        "in_chip_atlas": st.column_config.CheckboxColumn(),
        "title": st.column_config.TextColumn(width="large"),
    },
)

# --- Take-out actions ---
st.subheader("Take out")

tsv_col, link_col = st.columns([1, 2])
with tsv_col:
    tsv = df.to_csv(sep="\t", index=False).encode("utf-8")
    st.download_button(
        "Download cohort as TSV",
        data=tsv,
        file_name="cohort.tsv",
        mime="text/tab-separated-values",
    )
with link_col:
    st.markdown(
        "**DDBJ Search**: open per-sample BioSample details "
        "(`https://ddbj-search.dbcls.jp/resource/biosample/{accession}`)"
    )

# --- Per-sample external links ---
st.caption("Per-sample external links (first 200 shown):")
links_df = pd.DataFrame(
    {
        "accession": df["accession"],
        "DDBJ Search": df["accession"].map(
            lambda a: f"https://ddbj-search.dbcls.jp/resource/biosample/{a}"
        ),
        "ChIP-Atlas top (hint)": df["in_chip_atlas"].map(
            lambda b: "https://chip-atlas.org/" if b else ""
        ),
    }
).head(200)

st.dataframe(
    links_df,
    width="stretch",
    hide_index=True,
    column_config={
        "DDBJ Search": st.column_config.LinkColumn(
            "DDBJ Search", display_text="open"
        ),
        "ChIP-Atlas top (hint)": st.column_config.LinkColumn(
            "ChIP-Atlas (hint)", display_text="open"
        ),
    },
)
st.caption(
    "ChIP-Atlas Peak Browser deep links (per-SRX) are out of PoC scope — "
    "see `docs/data-model.md` for the rationale."
)
