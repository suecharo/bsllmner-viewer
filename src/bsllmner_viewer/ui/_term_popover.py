"""Shared `st.popover` renderer for term info across the 3 pages.

See ``docs/ui.md`` § "Term info popover (3 画面共通)" for the spec.
"""

from __future__ import annotations

import duckdb
import streamlit as st

from bsllmner_viewer.lib.aggregation import (
    FIELD_TO_ONTOLOGY,
    SampleFilters,
    term_sample_count,
)
from bsllmner_viewer.lib.ontology import (
    TermSummary,
    external_url,
    term_summary,
)


def render_term_popover(
    con: duckdb.DuckDBPyConnection,
    field: str,
    term_id: str,
    label: str | None,
    filters: SampleFilters,
    *,
    summary: TermSummary | None = None,
) -> None:
    """Render one term as an ``st.popover`` button.

    Opening the popover reveals: ``label (term_id)`` header, ontology metadata
    (source / depth, with ``inferred`` fallback when the term isn't in
    ``ontology.parquet``), the BioSample count under the current filters, and
    an external link to the ontology's official term page (if the prefix is
    known).

    Pass a pre-fetched ``summary`` to avoid the per-term ``ontology.parquet``
    lookup — pages that render many popovers (Gap Discovery axis terms,
    cohort constraints) batch-fetch via ``term_summaries`` so a click rerun
    isn't N independent queries.
    """
    if summary is None:
        summary = term_summary(con, term_id)
    shown_label = summary.label or label or term_id
    with st.popover(f"ℹ {shown_label}", help=f"{field} · {term_id}"):
        _render_body(con, field, term_id, shown_label, summary, filters)


def _render_body(
    con: duckdb.DuckDBPyConnection,
    field: str,
    term_id: str,
    shown_label: str,
    summary: TermSummary,
    filters: SampleFilters,
) -> None:
    st.markdown(f"**{shown_label}** &nbsp;`{term_id}`")

    meta_bits: list[str] = []
    if summary.ontology_source:
        meta_bits.append(f"source: `{summary.ontology_source}`")
    elif field in FIELD_TO_ONTOLOGY:
        meta_bits.append(f"source: `{FIELD_TO_ONTOLOGY[field]}` (inferred)")
    if summary.depth is not None:
        meta_bits.append(f"depth: {summary.depth}")
    if meta_bits:
        st.caption(" · ".join(meta_bits))

    n_sample, n_chip = term_sample_count(con, field, term_id, filters)
    col_total, col_chip = st.columns(2)
    col_total.metric("BioSamples (filtered)", f"{n_sample:,}")
    col_chip.metric("of which ChIP-Atlas", f"{n_chip:,}")

    link = external_url(term_id)
    if link is not None:
        site_label, url = link
        st.markdown(f"[Open in {site_label} ↗]({url})")
    else:
        st.caption("No external link for this term prefix.")
