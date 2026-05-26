from __future__ import annotations

from typing import Any, cast

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    VALID_FIELDS,
    SampleFilters,
    cohort_breakdown,
    cohort_count,
    cohort_facts_columns,
    cohort_overlap_summary,
    cohort_samples,
    cohort_srx_links,
    cohort_term_overlap,
)
from bsllmner_viewer.lib.ontology import term_summaries
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
    # 1 batch ontology lookup for all cohort-constraint terms, so a sidebar
    # rerun doesn't fan out into N independent term_summary queries.
    cohort_term_summaries = term_summaries(
        con, list({tid for _, tid in _cohort_terms})
    )
    for (field_name, term_id), _ in _cohort_terms.items():
        render_term_popover(
            con, field_name, term_id, None, filters,
            summary=cohort_term_summaries.get(term_id),
        )

total = cohort_count(
    con, filters, facts_terms=facts_terms, facts_cells=facts_cells
)
st.metric("Total BioSamples", f"{total:,}")

if total == 0:
    st.warning("No samples match the current cohort.")
    st.stop()

# 3-up mini histograms: cohort outline (year / organism / source). Aggregates
# the whole cohort in SQL via cohort_breakdown so the 10K table cap below
# doesn't truncate the histograms — they always reflect the full `total`.
breakdown = cohort_breakdown(
    con, filters, facts_terms=facts_terms, facts_cells=facts_cells
)
if not breakdown.empty:
    year_df = (
        breakdown.groupby("submission_year", as_index=False)["sample_count"]
        .sum()
        .sort_values("submission_year")
    )
    org_df = (
        breakdown.groupby("organism_normalized", as_index=False)["sample_count"]
        .sum()
        .sort_values("sample_count", ascending=False)
    )
    src_df = (
        breakdown.groupby("source_system", as_index=False)["sample_count"]
        .sum()
        .sort_values("sample_count", ascending=False)
    )
    mini_year, mini_org, mini_src = st.columns(3)
    with mini_year:
        st.caption("Submission year")
        fig_year = px.bar(
            year_df,
            x="submission_year",
            y="sample_count",
            labels={
                "submission_year": "year",
                "sample_count": "BioSample",
            },
        )
        fig_year.update_layout(
            height=180,
            margin={"l": 0, "r": 0, "t": 4, "b": 0},
            showlegend=False,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_year, width="stretch", key="cohort_mini_year")
    with mini_org:
        st.caption("Organism")
        fig_org = px.bar(
            org_df,
            x="organism_normalized",
            y="sample_count",
            labels={
                "organism_normalized": "organism",
                "sample_count": "BioSample",
            },
        )
        fig_org.update_layout(
            height=180,
            margin={"l": 0, "r": 0, "t": 4, "b": 0},
            showlegend=False,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_org, width="stretch", key="cohort_mini_org")
    with mini_src:
        st.caption("Source system")
        fig_src = px.bar(
            src_df,
            x="source_system",
            y="sample_count",
            labels={"source_system": "source", "sample_count": "BioSample"},
        )
        fig_src.update_layout(
            height=180,
            margin={"l": 0, "r": 0, "t": 4, "b": 0},
            showlegend=False,
            modebar={"remove": ["fullscreen", "togglefullscreen"]},
        )
        st.plotly_chart(fig_src, width="stretch", key="cohort_mini_src")

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

# --- C4: Pinned cohort comparison ---
# Pin the current cohort, then later (re-)visit a different one to see the
# overlap. The pin snapshots the accession list at pin-time so subsequent
# filter / facts changes don't quietly drift the comparison.
current_accessions: list[str] = df["accession"].astype(str).tolist()

with st.expander("🔖 Pinned cohort", expanded=False):
    pinned_label = cast(str | None, st.session_state.get("pinned_cohort_label"))
    pinned_accs = cast(
        list[str] | None, st.session_state.get("pinned_cohort_accessions")
    )

    if pinned_label and pinned_accs:
        meta_col, clear_col = st.columns([3, 1])
        with meta_col:
            st.markdown(
                f"**Pinned:** {pinned_label} — {len(pinned_accs):,} BioSamples"
            )
        with clear_col:
            if st.button("Clear pin", key="pinned_clear"):
                st.session_state.pop("pinned_cohort_label", None)
                st.session_state.pop("pinned_cohort_accessions", None)
                st.session_state.pop("pinned_cohort_compare_on", None)
                st.rerun()

        compare_on = st.toggle(
            "Compare with pinned",
            value=bool(st.session_state.get("pinned_cohort_compare_on", False)),
            key="pinned_compare_toggle",
            help=(
                "Show 3-way set arithmetic (Pinned only / Both / Current only) "
                "and per-field Jaccard overlap. Comparison runs over the "
                "displayed 10K cap of the current cohort."
            ),
        )
        st.session_state["pinned_cohort_compare_on"] = compare_on

        if compare_on:
            summary = cohort_overlap_summary(pinned_accs, current_accessions)
            m_pin, m_both, m_cur = st.columns(3)
            m_pin.metric("Pinned only", f"{summary['only_a']:,}")
            m_both.metric("Both", f"{summary['both']:,}")
            m_cur.metric("Current only", f"{summary['only_b']:,}")

            # 2-set Venn via Plotly shapes. Two semi-transparent circles +
            # 3 text annotations for the 3 region counts. Pure Plotly so no
            # extra dependency.
            venn_fig = go.Figure()
            venn_fig.add_shape(
                type="circle",
                xref="x", yref="y",
                x0=0.05, x1=0.55, y0=0.2, y1=0.8,
                fillcolor="rgba(31,119,180,0.35)",
                line={"color": "rgba(31,119,180,0.8)"},
            )
            venn_fig.add_shape(
                type="circle",
                xref="x", yref="y",
                x0=0.45, x1=0.95, y0=0.2, y1=0.8,
                fillcolor="rgba(255,127,14,0.35)",
                line={"color": "rgba(255,127,14,0.8)"},
            )
            venn_fig.add_annotation(
                x=0.2, y=0.5, text=f"Pinned only<br>{summary['only_a']:,}",
                showarrow=False, font={"size": 14, "color": "white"},
            )
            venn_fig.add_annotation(
                x=0.5, y=0.5, text=f"Both<br>{summary['both']:,}",
                showarrow=False, font={"size": 14, "color": "white"},
            )
            venn_fig.add_annotation(
                x=0.8, y=0.5, text=f"Current only<br>{summary['only_b']:,}",
                showarrow=False, font={"size": 14, "color": "white"},
            )
            venn_fig.update_xaxes(
                visible=False, range=[0, 1], constrain="domain"
            )
            venn_fig.update_yaxes(
                visible=False, range=[0, 1], scaleanchor="x", scaleratio=1,
            )
            venn_fig.update_layout(
                height=260,
                margin={"l": 4, "r": 4, "t": 4, "b": 4},
                showlegend=False,
                modebar={"remove": ["fullscreen", "togglefullscreen"]},
            )
            st.plotly_chart(
                venn_fig, width="stretch", key="pinned_venn"
            )

            # Per-field Jaccard overlap. Compares the *term sets* derived
            # from each cohort's facts so the user sees not just sample
            # overlap but compositional similarity per ontology field.
            overlap_df = cohort_term_overlap(
                con, pinned_accs, current_accessions
            )
            overlap_df = overlap_df[
                (overlap_df["n_pinned"] > 0) | (overlap_df["n_current"] > 0)
            ]
            if overlap_df.empty:
                st.caption(
                    "Neither cohort has any ontology-mapped facts to compare."
                )
            else:
                fig_jac = px.bar(
                    overlap_df.sort_values("jaccard", ascending=True),
                    x="jaccard",
                    y="field",
                    orientation="h",
                    range_x=[0, 1],
                    hover_data={
                        "n_pinned": True,
                        "n_current": True,
                        "n_both": True,
                    },
                    labels={
                        "jaccard": "Jaccard (|A∩B| / |A∪B|)",
                        "field": "Field",
                        "n_pinned": "Terms in pinned",
                        "n_current": "Terms in current",
                        "n_both": "Terms in both",
                    },
                )
                fig_jac.update_layout(
                    height=max(220, 32 * len(overlap_df)),
                    margin={"l": 4, "r": 4, "t": 4, "b": 4},
                    modebar={"remove": ["fullscreen", "togglefullscreen"]},
                )
                st.plotly_chart(
                    fig_jac,
                    width="stretch",
                    key="pinned_jaccard",
                )
                st.caption(
                    "Per-field term overlap. Jaccard=1 means both cohorts "
                    "use identical term sets in that field; Jaccard=0 means "
                    "disjoint compositions."
                )
    else:
        if st.button(
            "Pin this cohort",
            type="primary",
            key="pinned_set",
            disabled=not current_accessions,
            help=(
                "Snapshot the current cohort so you can switch to another "
                "view and compare with it."
            ),
        ):
            snapshot_label = (
                label
                if label
                else f"Cohort of {total:,} BioSamples"
            )
            st.session_state["pinned_cohort_label"] = snapshot_label
            st.session_state["pinned_cohort_accessions"] = list(current_accessions)
            st.session_state["pinned_cohort_compare_on"] = False
            st.rerun()
        else:
            st.caption(
                "No cohort is pinned. Click *Pin this cohort* to snapshot "
                "the current selection, then visit another cohort to "
                "compare."
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

with st.expander(
    f"Ontology columns ({len(VALID_FIELDS)} available)", expanded=False
):
    selected_fields = st.multiselect(
        "Show these ontology fields as columns",
        options=list(VALID_FIELDS),
        default=list(VALID_FIELDS),
        key="cohort_ontology_columns",
        help=(
            "Each cell shows `label (term_id)` for every term the BioSample "
            "was mapped to in that field — deduplicated, sorted by label, "
            "comma-joined. Aggregated across every run."
        ),
    )

if selected_fields:
    facts_cols_df = cohort_facts_columns(
        con, df["accession"].tolist(), selected_fields
    )
    if not facts_cols_df.empty:
        pivot = facts_cols_df.pivot(
            index="accession", columns="field", values="value"
        ).reset_index()
        df = df.merge(pivot, on="accession", how="left")
    for f in selected_fields:
        if f not in df.columns:
            df[f] = ""
    df[selected_fields] = df[selected_fields].fillna("")

display_cols = [
    "accession",
    "organism_normalized",
    "submission_year",
    "title",
    "source_system",
    "in_chip_atlas",
    "srx",
    "srx_display",
    *selected_fields,
]

st.dataframe(
    df[display_cols],
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
        **{
            f: st.column_config.TextColumn(f, width="medium")
            for f in selected_fields
        },
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


# --- Per-SRX deep links (expanded to 1 row per SRX, so +N more are reachable) ---
st.subheader("Per-SRX deep links")

SRX_LIMIT = 500
cohort_accessions = df["accession"].tolist()
srx_df = cohort_srx_links(con, cohort_accessions, limit=SRX_LIMIT)

if srx_df.empty:
    st.caption(
        "No SRX records for this cohort — none of the cohort BioSamples "
        "have SRA Experiments registered."
    )
else:
    unique_bs = int(srx_df["accession"].nunique())
    st.caption(
        f"Expanded to 1 row per SRX. Showing {len(srx_df):,} SRX "
        f"from {unique_bs:,} BioSamples. Rows beyond the main table's "
        "`+N more` are surfaced here so every SRX is clickable."
    )
    if len(srx_df) >= SRX_LIMIT:
        st.warning(
            f"Capped at {SRX_LIMIT:,} SRX rows. Refine filters to narrow "
            "down the cohort if you need to drill into more SRX."
        )

    srx_df = srx_df.assign(
        **{
            "BioSample (DDBJ)": srx_df["accession"].map(
                lambda a: f"https://ddbj-search.dbcls.jp/resource/biosample/{a}"
            ),
            "SRX (NCBI)": srx_df["srx"].map(_ncbi_sra_url),
            "SRX (DDBJ)": srx_df["srx"].map(_ddbj_sra_url),
            "ChIP-Atlas BigWig": [
                _chip_atlas_url(s, g, "bw")
                for s, g in zip(
                    srx_df["srx"], srx_df["chip_atlas_genome"], strict=True
                )
            ],
            "ChIP-Atlas Peak BED": [
                _chip_atlas_url(s, g, "bed")
                for s, g in zip(
                    srx_df["srx"], srx_df["chip_atlas_genome"], strict=True
                )
            ],
        }
    )

    st.dataframe(
        srx_df[
            [
                "accession",
                "srx",
                "bioproject",
                "sra_study",
                "sra_sample",
                "status",
                "BioSample (DDBJ)",
                "SRX (NCBI)",
                "SRX (DDBJ)",
                "ChIP-Atlas BigWig",
                "ChIP-Atlas Peak BED",
            ]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "accession": st.column_config.TextColumn("BioSample", width="small"),
            "srx": st.column_config.TextColumn("SRX", width="small"),
            "bioproject": st.column_config.TextColumn(width="small"),
            "sra_study": st.column_config.TextColumn(width="small"),
            "sra_sample": st.column_config.TextColumn(width="small"),
            "status": st.column_config.TextColumn(width="small"),
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
    "`chip_atlas_genome` (samples 系統). The SRX may not actually exist in "
    "ChIP-Atlas (HTTP 404 possible). BigWig / Peak BED columns are empty "
    "for samples whose 系統 is not chip-atlas."
)
