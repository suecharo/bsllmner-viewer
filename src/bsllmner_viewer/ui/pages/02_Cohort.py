from __future__ import annotations

from typing import Any, cast

import pandas as pd
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    SampleFilters,
    cohort_count,
    cohort_samples,
)
from bsllmner_viewer.ui._conn import conn
from bsllmner_viewer.ui._filters import sidebar_filters
from bsllmner_viewer.ui._term_popover import render_term_popover

st.set_page_config(page_title="Cohort — bsllmner-viewer", layout="wide")

st.title("Cohort drill-down")

con = conn()

filters_default = cast(
    SampleFilters | None, st.session_state.get("cohort_filters")
)
facts_terms = cast(
    list[tuple[str, str]] | None, st.session_state.get("cohort_facts_terms")
)
facts_cells = cast(
    list[list[tuple[str, str]]] | None,
    st.session_state.get("cohort_facts_cells"),
)
label = cast(str | None, st.session_state.get("cohort_label"))

if label:
    st.info(f"Cohort: {label}")
elif facts_cells:
    formatted = " OR ".join(
        "(" + " AND ".join(f"{f}={t}" for f, t in cell) + ")"
        for cell in facts_cells
    )
    st.info(f"Cohort: {formatted}")
elif facts_terms:
    formatted = ", ".join(f"{f}={t}" for f, t in facts_terms)
    st.info(f"Cohort: {formatted}")
else:
    st.caption("No cohort selected. Use the Gap Discovery page or filter directly.")

# Collapse the cohort's (field, term_id) constraints into one popover per
# unique term so users can inspect ontology metadata without re-opening
# Gap Discovery. The render uses the session-derived cohort, not the
# filtered sample set — counts inside the popover do honour the sidebar
# filters via `term_sample_count`.
_cohort_terms: dict[tuple[str, str], None] = {}
if facts_terms:
    for f, t in facts_terms:
        _cohort_terms.setdefault((f, t), None)
if facts_cells:
    for cell in facts_cells:
        for f, t in cell:
            _cohort_terms.setdefault((f, t), None)

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

if _cohort_terms:
    st.caption("Term info for cohort constraints:")
    for (field_name, term_id), _ in _cohort_terms.items():
        render_term_popover(con, field_name, term_id, None, filters)

total = cohort_count(
    con, filters, facts_terms=facts_terms, facts_cells=facts_cells
)
st.metric("Total BioSamples", f"{total:,}")

if total == 0:
    st.warning("No samples match the current cohort.")
    st.stop()

LIMIT = 10000
df = cohort_samples(
    con,
    filters,
    facts_terms=facts_terms,
    facts_cells=facts_cells,
    limit=LIMIT,
)
if total > LIMIT:
    st.warning(
        f"Showing the first {LIMIT:,} of {total:,} samples (sorted by year desc). "
        "Refine filters to narrow down."
    )


def _srx_display(srx: object, srx_count: Any) -> str:
    if not isinstance(srx, str) or not srx:
        return ""
    try:
        n = int(srx_count) if srx_count is not None else 0
    except (TypeError, ValueError):
        n = 0
    return f"{srx} (+{n - 1} more)" if n > 1 else srx


df["srx_display"] = [
    _srx_display(s, c) for s, c in zip(df["srx"], df["srx_count"], strict=True)
]

st.dataframe(
    df.drop(columns=["srx_count"]),
    width="stretch",
    hide_index=True,
    column_config={
        "accession": st.column_config.TextColumn(width="small"),
        "in_chip_atlas": st.column_config.CheckboxColumn(),
        "title": st.column_config.TextColumn(width="large"),
        "srx": st.column_config.TextColumn("srx (first)", width="small"),
        "srx_display": st.column_config.TextColumn(
            "SRX (display)", width="medium",
            help="First SRX accession, plus +N more when the BioSample has additional SRX rows.",
        ),
        "chip_atlas_genome": st.column_config.TextColumn(width="small"),
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
        "TSV includes the **first SRX** per BioSample (plus `srx_count`). "
        "Use the link table below for clickable per-SRX deep links "
        "(NCBI SRA / DDBJ Search / ChIP-Atlas BigWig / Peak BED)."
    )


def _ncbi_sra_url(srx: object) -> str:
    if not isinstance(srx, str) or not srx:
        return ""
    return f"https://www.ncbi.nlm.nih.gov/sra/?term={srx}"


def _ddbj_sra_url(srx: object) -> str:
    if not isinstance(srx, str) or not srx:
        return ""
    return f"https://ddbj-search.dbcls.jp/resource/sra-experiment/{srx}"


def _chip_atlas_url(srx: object, genome: object, kind: str) -> str:
    if not isinstance(srx, str) or not srx:
        return ""
    if not isinstance(genome, str) or not genome:
        return ""
    if kind == "bw":
        return f"https://chip-atlas.dbcls.jp/data/{genome}/eachData/bw/{srx}.bw"
    return f"https://chip-atlas.dbcls.jp/data/{genome}/eachData/bed05/{srx}.05.bed"


# --- Per-sample external links ---
st.caption("Per-sample external links (first 200 shown):")
links_df = pd.DataFrame(
    {
        "accession": df["accession"],
        "srx": df["srx"],
        "BioSample (DDBJ)": df["accession"].map(
            lambda a: f"https://ddbj-search.dbcls.jp/resource/biosample/{a}"
        ),
        "SRX (NCBI)": df["srx"].map(_ncbi_sra_url),
        "SRX (DDBJ)": df["srx"].map(_ddbj_sra_url),
        "ChIP-Atlas BigWig": [
            _chip_atlas_url(s, g, "bw")
            for s, g in zip(df["srx"], df["chip_atlas_genome"], strict=True)
        ],
        "ChIP-Atlas Peak BED": [
            _chip_atlas_url(s, g, "bed")
            for s, g in zip(df["srx"], df["chip_atlas_genome"], strict=True)
        ],
    }
).head(200)

st.dataframe(
    links_df,
    width="stretch",
    hide_index=True,
    column_config={
        "accession": st.column_config.TextColumn(width="small"),
        "srx": st.column_config.TextColumn("first SRX", width="small"),
        "BioSample (DDBJ)": st.column_config.LinkColumn(
            "BioSample (DDBJ)", display_text="open"
        ),
        "SRX (NCBI)": st.column_config.LinkColumn(
            "SRX (NCBI)", display_text="open"
        ),
        "SRX (DDBJ)": st.column_config.LinkColumn(
            "SRX (DDBJ)", display_text="open"
        ),
        "ChIP-Atlas BigWig": st.column_config.LinkColumn(
            "ChIP-Atlas BigWig", display_text="open"
        ),
        "ChIP-Atlas Peak BED": st.column_config.LinkColumn(
            "ChIP-Atlas Peak BED", display_text="open"
        ),
    },
)
st.caption(
    "ChIP-Atlas BigWig / Peak BED are best-effort URLs built from `srx` + "
    "`chip_atlas_genome` (samples系統). The SRX may not actually exist in "
    "ChIP-Atlas (HTTP 404 possible). BigWig / Peak BED columns are empty "
    "for samples whose系統 is not chip-atlas."
)
