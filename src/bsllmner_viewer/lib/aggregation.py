"""Heatmap and bubble aggregation queries against samples + facts parquet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import duckdb
import numpy as np
import pandas as pd

VALID_FIELDS: Final[tuple[str, ...]] = (
    "cell_line",
    "cell_type",
    "tissue",
    "disease",
    "drug",
    "knockout_gene",
    "knockdown_gene",
    "overexpressed_gene",
)

# Sentinel that the UI (sidebar multiselect) and the agg parquet share to stand
# in for NULL. samples.parquet keeps real NULLs; the live ``_filter_clauses``
# expands ``UNKNOWN`` in an IN list to ``OR <col> IS NULL`` so the live and
# agg paths return the same row set under any filter (docs/data-model.md
# §"sequence_type の null/mixed/(unknown) 取扱").
UNKNOWN: Final[str] = "(unknown)"

# Per-field ontology used for hierarchy roll-up. Fields whose primary ontology
# has no usable is-a hierarchy (Cellosaurus: self-loop only) or is not stored
# in ontology.parquet (NCBI Gene) are intentionally absent — depth roll-up is
# not offered for them.
FIELD_TO_ONTOLOGY: Final[dict[str, str]] = {
    "disease": "MONDO",
    "cell_type": "CL",
    "tissue": "UBERON",
    "drug": "ChEBI",
}

# Overlay axis predicates for ``gap_heatmap_pivot`` and other secondary_count
# users. Key = ID shown to UI / persisted in session_state; value = (label,
# SQL predicate with ``s.`` qualifier, extra params). The first entry is the
# default. ``seq:<value>`` is recognised dynamically (see _overlay_predicate)
# so adding a new sequence_type does not need a code change here.
OVERLAY_AXES: Final[dict[str, tuple[str, str, tuple[object, ...]]]] = {
    "chip_atlas": ("ChIP-Atlas", "s.source_system LIKE 'chip-atlas-%'", ()),
}


def can_roll_up(field_name: str) -> bool:
    return field_name in FIELD_TO_ONTOLOGY


def _overlay_predicate(overlay_axis: str | None) -> tuple[str, list[object]]:
    if overlay_axis is None:
        overlay_axis = next(iter(OVERLAY_AXES))
    if overlay_axis.startswith("seq:"):
        return "s.sequence_type = ?", [overlay_axis[4:]]
    spec = OVERLAY_AXES.get(overlay_axis, OVERLAY_AXES[next(iter(OVERLAY_AXES))])
    return spec[1], list(spec[2])


@dataclass(frozen=True)
class SampleFilters:
    """Hashable snapshot of sidebar filter state.

    ``@st.cache_data`` uses this object as a cache key directly because every
    list-shaped field is a tuple and the dataclass is frozen. UI code MUST
    pass tuples (not lists) when constructing.
    """

    organism_normalized: tuple[str, ...] = ()
    submission_year_min: int | None = None
    submission_year_max: int | None = None
    source_system: tuple[str, ...] = ()
    sequence_type: tuple[str, ...] = ()


def _in_clause_with_unknown(
    col_sql: str, values: tuple[str, ...]
) -> tuple[str, list[object]]:
    """IN clause that also matches NULL when ``UNKNOWN`` is part of ``values``.

    Without the OR expansion the live path silently drops NULL rows whenever
    any value is selected, while the agg path collapses NULL into ``UNKNOWN``
    — the asymmetry behind QUA-1 / QUA-3.
    """
    placeholders = ",".join(["?"] * len(values))
    base = f"{col_sql} IN ({placeholders})"
    if UNKNOWN in values:
        return f"({base} OR {col_sql} IS NULL)", list(values)
    return base, list(values)


def _filter_clauses(f: SampleFilters) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if f.organism_normalized:
        clause, p = _in_clause_with_unknown(
            "s.organism_normalized", f.organism_normalized
        )
        clauses.append(clause)
        params.extend(p)
    if f.submission_year_min is not None:
        clauses.append("s.submission_year >= ?")
        params.append(f.submission_year_min)
    if f.submission_year_max is not None:
        clauses.append("s.submission_year <= ?")
        params.append(f.submission_year_max)
    if f.source_system:
        # source_system has no NULLs by construction — plain IN.
        placeholders = ",".join(["?"] * len(f.source_system))
        clauses.append(f"s.source_system IN ({placeholders})")
        params.extend(f.source_system)
    if f.sequence_type:
        clause, p = _in_clause_with_unknown("s.sequence_type", f.sequence_type)
        clauses.append(clause)
        params.extend(p)
    if not clauses:
        return "TRUE", []
    return " AND ".join(clauses), params


def _validate_field(name: str) -> None:
    if name not in VALID_FIELDS:
        raise ValueError(f"unknown field: {name!r}")


def _axis_facts_sql(
    field_name: str, roll_up_depth: int | None
) -> tuple[str, list[object]]:
    """Build a subquery yielding (accession, run_name, term_id, label) for a field.

    When ``roll_up_depth`` is None or the field's ontology has no usable
    hierarchy, term_id stays at the leaf level. Otherwise each leaf is mapped
    to ``MIN(parent_term_id)`` at the requested depth in
    ``FIELD_TO_ONTOLOGY[field_name]`` (deterministic across DAG parents).
    Leaves shallower than the requested depth keep their own term_id (no
    matching ancestor → ``COALESCE`` fallback).
    """
    if roll_up_depth is None or not can_roll_up(field_name):
        return (
            "SELECT f.accession, f.run_name, f.term_id, f.label "
            "FROM facts f "
            "WHERE f.field = ? AND f.term_id IS NOT NULL",
            [field_name],
        )
    source = FIELD_TO_ONTOLOGY[field_name]
    # ontology.depth is the depth of `term_id` (not of `parent_term_id`), so to
    # find an ancestor at the requested depth we restrict `parent_term_id` to
    # the set of terms whose depth = roll_up_depth in the same ontology_source.
    # MIN() is deterministic when DAGs surface multiple depth-N ancestors.
    return (
        "SELECT f.accession, f.run_name, "
        "       COALESCE(r.rolled_to, f.term_id) AS term_id, "
        "       COALESCE(o_lbl.label, f.label) AS label "
        "FROM facts f "
        "LEFT JOIN ("
        "  SELECT t.term_id, MIN(t.parent_term_id) AS rolled_to "
        "  FROM ontology t "
        "  WHERE t.ontology_source = ? "
        "    AND t.parent_term_id IN ("
        "      SELECT term_id FROM ontology "
        "      WHERE depth = ? AND ontology_source = ?"
        "    ) "
        "  GROUP BY t.term_id"
        ") r ON r.term_id = f.term_id "
        "LEFT JOIN ontology o_lbl "
        "  ON o_lbl.term_id = r.rolled_to "
        "  AND o_lbl.parent_term_id = o_lbl.term_id "
        "WHERE f.field = ? AND f.term_id IS NOT NULL",
        [source, roll_up_depth, source, field_name],
    )


def top_terms(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int,
    roll_up_depth: int | None = None,
) -> list[tuple[str, str]]:
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    axis_sql, axis_params = _axis_facts_sql(field_name, roll_up_depth)
    sql = (
        f"WITH fx AS ({axis_sql}) "
        "SELECT fx.term_id, ANY_VALUE(fx.label) AS lbl "
        "FROM fx "
        "JOIN samples s ON s.accession = fx.accession AND s.run_name = fx.run_name "
        f"WHERE {where_clause} "
        "GROUP BY fx.term_id "
        "ORDER BY COUNT(DISTINCT s.accession) DESC "
        "LIMIT ?"
    )
    rows = con.execute(sql, [*axis_params, *where_params, top_n]).fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def gap_heatmap_pivot(
    con: duckdb.DuckDBPyConnection,
    x_field: str,
    y_field: str,
    filters: SampleFilters,
    top_n_x: int = 30,
    top_n_y: int = 30,
    x_roll_up_depth: int | None = None,
    y_roll_up_depth: int | None = None,
    overlay_axis: str | None = None,
) -> pd.DataFrame:
    """Return a long-form DataFrame of (x_term, y_term) sample counts.

    Columns: ``x_term_id``, ``x_label``, ``y_term_id``, ``y_label``,
    ``sample_count``, ``secondary_count``. ``secondary_count`` is the
    ``COUNT(DISTINCT accession)`` of rows that also match ``overlay_axis``
    (default: ChIP-Atlas systems). See ``OVERLAY_AXES`` and the UI's
    "Overlay axis" selector.

    ``x_roll_up_depth`` / ``y_roll_up_depth``: when set, replace each leaf
    term with ``MIN(parent_term_id)`` at that depth in the field's primary
    ontology (see ``FIELD_TO_ONTOLOGY``). Fields outside ``FIELD_TO_ONTOLOGY``
    or with no eligible ancestor fall back to the leaf term.

    Only cells with sample_count > 0 are returned; the caller pivots and
    reindexes against the chosen axis term lists to surface empty cells.

    The internal ``top_terms`` axis-selection runs against the fast path
    (``top_terms_fast``) when ``has_dashboard_aggregates(con)`` and the field
    has no roll-up, falling back to the live ``top_terms`` otherwise.
    """
    _validate_field(x_field)
    _validate_field(y_field)

    x_pairs = _select_top_terms(con, x_field, filters, top_n_x, x_roll_up_depth)
    y_pairs = _select_top_terms(con, y_field, filters, top_n_y, y_roll_up_depth)
    if not x_pairs or not y_pairs:
        return pd.DataFrame(
            columns=[
                "x_term_id",
                "x_label",
                "y_term_id",
                "y_label",
                "sample_count",
                "secondary_count",
            ]
        )

    where_clause, where_params = _filter_clauses(filters)
    x_axis_sql, x_axis_params = _axis_facts_sql(x_field, x_roll_up_depth)
    y_axis_sql, y_axis_params = _axis_facts_sql(y_field, y_roll_up_depth)
    overlay_sql, overlay_params = _overlay_predicate(overlay_axis)
    x_terms = [t for t, _ in x_pairs]
    y_terms = [t for t, _ in y_pairs]
    x_ph = ",".join(["?"] * len(x_terms))
    y_ph = ",".join(["?"] * len(y_terms))

    sql = (
        f"WITH fx AS ({x_axis_sql}), fy AS ({y_axis_sql}) "
        "SELECT fx.term_id AS x_term_id, ANY_VALUE(fx.label) AS x_label, "
        "       fy.term_id AS y_term_id, ANY_VALUE(fy.label) AS y_label, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        f"       COUNT(DISTINCT CASE WHEN {overlay_sql} THEN s.accession END) "
        "         AS secondary_count "
        "FROM fx "
        "JOIN fy ON fx.accession = fy.accession AND fx.run_name = fy.run_name "
        "JOIN samples s ON s.accession = fx.accession AND s.run_name = fx.run_name "
        f"WHERE fx.term_id IN ({x_ph}) AND fy.term_id IN ({y_ph}) AND {where_clause} "
        "GROUP BY fx.term_id, fy.term_id"
    )
    params: list[object] = [
        *x_axis_params,
        *y_axis_params,
        *overlay_params,
        *x_terms,
        *y_terms,
        *where_params,
    ]
    df = con.execute(sql, params).fetchdf()
    return df


def _select_top_terms(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int,
    roll_up_depth: int | None,
) -> list[tuple[str, str]]:
    """Pick the fast path when it's safe (no roll-up + agg parquet present).

    The fast path is implemented by ``top_terms_fast`` (defined further down)
    and uses ``agg_field_term_dims``. When the caller asks for a roll-up
    depth, only the live ``top_terms`` knows how to walk the ontology, so we
    keep the live call.
    """
    if roll_up_depth is None and has_dashboard_aggregates(con):
        return top_terms_fast(con, field_name, filters, top_n)
    return top_terms(con, field_name, filters, top_n, roll_up_depth)


def _facts_terms_clauses(
    facts_terms: list[tuple[str, str]] | None,
) -> tuple[list[str], list[object]]:
    if not facts_terms:
        return [], []
    for field_name, _ in facts_terms:
        _validate_field(field_name)
    clauses: list[str] = []
    params: list[object] = []
    for field_name, term_id in facts_terms:
        clauses.append(
            "EXISTS (SELECT 1 FROM facts f WHERE f.accession = s.accession "
            "AND f.run_name = s.run_name AND f.field = ? AND f.term_id = ?)"
        )
        params.extend([field_name, term_id])
    return clauses, params


def _facts_cells_clauses(
    facts_cells: list[list[tuple[str, str]]] | None,
) -> tuple[list[str], list[object]]:
    """Build OR-of-AND clauses for cell-shaped selections.

    Each inner list is a single heatmap cell — its (field, term_id) entries
    must all match (AND). Cells are joined with OR so multiple picks form a
    union cohort. Empty cells are skipped to avoid emitting an empty AND that
    would short-circuit the OR.
    """
    if not facts_cells:
        return [], []
    or_parts: list[str] = []
    params: list[object] = []
    for cell in facts_cells:
        if not cell:
            continue
        sub_clauses, sub_params = _facts_terms_clauses(cell)
        or_parts.append("(" + " AND ".join(sub_clauses) + ")")
        params.extend(sub_params)
    if not or_parts:
        return [], []
    return ["(" + " OR ".join(or_parts) + ")"], params


def cohort_samples(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    facts_terms: list[tuple[str, str]] | None = None,
    facts_cells: list[list[tuple[str, str]]] | None = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """Return matching samples for a cohort.

    ``facts_terms`` is a list of (field, term_id) pairs that the sample must
    have. All pairs must match (AND semantics).

    ``facts_cells`` is a list of cells, where each cell is itself a list of
    (field, term_id) pairs combined with AND. Cells are combined with OR so
    multiple heatmap picks build a union cohort. ``facts_terms`` and
    ``facts_cells`` together are AND'd with the sample filters.

    The returned DataFrame carries ``srx`` (first SRX) and ``srx_count``
    inline so the main BioSample table is served from a single samples-only
    SELECT — no extra JOIN against ``srx_links`` (Cohort rerenders on every
    sidebar change and a JOIN would dominate latency). The per-SRX deep-link
    drill-down passes the resulting accession list to ``cohort_srx_links``.
    """
    where_clause, where_params = _filter_clauses(filters)
    base_params: list[object] = list(where_params)
    extra_clauses: list[str] = []

    term_clauses, term_params = _facts_terms_clauses(facts_terms)
    extra_clauses.extend(term_clauses)
    base_params.extend(term_params)

    cell_clauses, cell_params = _facts_cells_clauses(facts_cells)
    extra_clauses.extend(cell_clauses)
    base_params.extend(cell_params)

    if extra_clauses:
        where_clause = where_clause + " AND " + " AND ".join(extra_clauses)

    sql = (
        "SELECT s.accession, s.organism_normalized, s.submission_year, "
        "       s.title, s.source_system, s.sequence_type, "
        "       s.srx_first AS srx, s.srx_count "
        "FROM samples s "
        f"WHERE {where_clause} "
        "ORDER BY s.submission_year DESC, s.accession "
        "LIMIT ?"
    )
    return con.execute(sql, [*base_params, limit]).fetchdf()


_SRX_LINKS_COLS: Final[tuple[str, ...]] = (
    "accession",
    "srx",
    "bioproject",
    "sra_study",
    "sra_sample",
    "status",
    "source_system",
)


def cohort_srx_links(
    con: duckdb.DuckDBPyConnection,
    accessions: list[str],
    limit: int = 500,
) -> pd.DataFrame:
    """Return per-SRX rows for the given BioSamples (1 row per SRX).

    Joins ``srx_links`` to ``samples`` so each per-SRX row carries
    ``source_system`` (the UI uses ``lib/chip_atlas.bigwig_url`` /
    ``peak_bed_url`` to build deep links from that). Pre-filtering on the
    cohort's accession list keeps the scan bounded.

    Columns: ``accession`` / ``srx`` / ``bioproject`` / ``sra_study`` /
    ``sra_sample`` / ``status`` / ``source_system``.

    ``limit`` caps the SRX row count (default 500) so the UI never has to
    render an unbounded table.
    """
    if not accessions:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in _SRX_LINKS_COLS})
    sql = (
        "SELECT sl.accession, sl.srx, sl.bioproject, sl.sra_study, "
        "       sl.sra_sample, sl.status, s.source_system "
        "FROM srx_links sl "
        "JOIN samples s ON s.accession = sl.accession "
        "WHERE sl.accession IN (SELECT UNNEST(?::VARCHAR[])) "
        "ORDER BY sl.accession, sl.srx "
        "LIMIT ?"
    )
    return con.execute(sql, [accessions, limit]).fetchdf()


_FACTS_COLS_OUT: Final[tuple[str, ...]] = ("accession", "field", "value")


def cohort_facts_columns(
    con: duckdb.DuckDBPyConnection,
    accessions: list[str],
    fields: list[str],
) -> pd.DataFrame:
    """Return per-(accession, field) ontology labels for cohort table columns.

    Aggregates each (accession, field) into a single string of
    ``"label (term_id)"`` segments, deduplicated by ``term_id`` and sorted
    by the rendered string, joined with ``", "``. Used to render
    per-ontology-field columns in the Cohort drill-down table.

    When ``label`` is NULL the segment falls back to ``term_id`` alone
    (avoiding the redundant ``"MONDO:99 (MONDO:99)"`` output).

    Aggregation is run-agnostic: facts from every run for a given accession
    are union-merged, so the user sees every term the BioSample was
    annotated with regardless of which run produced it. Multiple labels for
    the same ``term_id`` across runs collapse to ``MIN(display)`` so the
    output stays deterministic.

    Returns long-form ``(accession, field, value)``. The UI pivots to one
    column per field and merges into the main cohort table on ``accession``.

    Empty when either ``accessions`` or ``fields`` is empty.
    """
    if not accessions or not fields:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in _FACTS_COLS_OUT})
    for f in fields:
        _validate_field(f)
    fields_ph = ",".join(["?"] * len(fields))
    sql = (
        "WITH per_term AS ("
        "  SELECT f.accession, f.field, f.term_id, "
        "         MIN(COALESCE(f.label || ' (' || f.term_id || ')', "
        "                      f.term_id)) AS display "
        "  FROM facts f "
        "  WHERE f.accession IN (SELECT UNNEST(?::VARCHAR[])) "
        f"    AND f.field IN ({fields_ph}) "
        "    AND f.term_id IS NOT NULL "
        "  GROUP BY f.accession, f.field, f.term_id"
        ") "
        "SELECT accession, field, "
        "       STRING_AGG(display, ', ' ORDER BY display) AS value "
        "FROM per_term "
        "GROUP BY accession, field"
    )
    return con.execute(sql, [accessions, *fields]).fetchdf()


def cohort_count(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    facts_terms: list[tuple[str, str]] | None = None,
    facts_cells: list[list[tuple[str, str]]] | None = None,
) -> int:
    where_clause, where_params = _filter_clauses(filters)
    base_params: list[object] = list(where_params)
    extra_clauses: list[str] = []

    term_clauses, term_params = _facts_terms_clauses(facts_terms)
    extra_clauses.extend(term_clauses)
    base_params.extend(term_params)

    cell_clauses, cell_params = _facts_cells_clauses(facts_cells)
    extra_clauses.extend(cell_clauses)
    base_params.extend(cell_params)

    if extra_clauses:
        where_clause = where_clause + " AND " + " AND ".join(extra_clauses)

    sql = f"SELECT COUNT(DISTINCT s.accession) FROM samples s WHERE {where_clause}"
    row = con.execute(sql, base_params).fetchone()
    return int(row[0]) if row else 0


def term_sample_count(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    term_id: str,
    filters: SampleFilters,
) -> tuple[int, int]:
    """Return ``(sample_count, secondary_count)`` for a single (field, term_id).

    Counts are taken under the supplied ``SampleFilters``. Both values reflect
    distinct ``s.accession``; ``secondary_count`` narrows to ChIP-Atlas
    systems via ``source_system LIKE 'chip-atlas-%'`` (the default overlay).
    """
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    sql = (
        "SELECT COUNT(DISTINCT s.accession), "
        "       COUNT(DISTINCT CASE WHEN s.source_system LIKE 'chip-atlas-%' "
        "         THEN s.accession END) "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession AND s.run_name = f.run_name "
        f"WHERE f.field = ? AND f.term_id = ? AND {where_clause}"
    )
    row = con.execute(sql, [field_name, term_id, *where_params]).fetchone()
    if row is None:
        return (0, 0)
    return (int(row[0] or 0), int(row[1] or 0))


def cumulative_bubble_dataset(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 15,
    roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Same as ``bubble_dataset`` but with per-year counts cumulatively summed.

    Each ``(term_id, organism_normalized)`` group is reindexed against the full
    contiguous year range present in the underlying data and filled with 0
    where no rows exist, then ``cumsum`` is applied. Cumulative columns are
    ``sample_count_cum`` / ``secondary_count_cum``; the original
    ``sample_count`` / ``secondary_count`` per-year values are kept.

    ``roll_up_depth`` matches ``bubble_dataset`` — leaves are replaced with the
    ancestor at the requested depth in ``FIELD_TO_ONTOLOGY[field_name]`` before
    the per-year aggregation runs.
    """
    df = bubble_dataset(con, field_name, filters, top_n, roll_up_depth)
    cum_columns = ["sample_count_cum", "secondary_count_cum"]
    if df.empty:
        for col in cum_columns:
            df[col] = pd.Series(dtype="int64")
        return df

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


def bubble_dataset(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 30,
    roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Aggregate (year, term) → sample count, with a configurable overlay.

    Returns columns: ``submission_year``, ``term_id``, ``label``,
    ``sample_count``, ``secondary_count``, ``organism_normalized``.
    ``secondary_count`` uses the default overlay axis (ChIP-Atlas systems).
    Future work can plumb ``overlay_axis`` through ``OVERLAY_AXES`` here too.

    ``roll_up_depth``: when set, replace each leaf term with its
    ``MIN(parent_term_id)`` ancestor at that depth in
    ``FIELD_TO_ONTOLOGY[field_name]`` (same semantics as ``gap_heatmap_pivot``
    and ``top_terms``). Fields outside ``FIELD_TO_ONTOLOGY`` keep the leaf
    term — the picker should be hidden by the UI in that case.
    """
    _validate_field(field_name)
    top_pairs = top_terms(con, field_name, filters, top_n, roll_up_depth)
    if not top_pairs:
        return pd.DataFrame(
            columns=[
                "submission_year",
                "term_id",
                "label",
                "organism_normalized",
                "sample_count",
                "secondary_count",
            ]
        )
    where_clause, where_params = _filter_clauses(filters)
    axis_sql, axis_params = _axis_facts_sql(field_name, roll_up_depth)
    overlay_sql, overlay_params = _overlay_predicate(None)
    term_ids = [t for t, _ in top_pairs]
    term_ph = ",".join(["?"] * len(term_ids))
    sql = (
        f"WITH fx AS ({axis_sql}) "
        "SELECT s.submission_year, fx.term_id, ANY_VALUE(fx.label) AS label, "
        "       s.organism_normalized, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        f"       COUNT(DISTINCT CASE WHEN {overlay_sql} THEN s.accession END) "
        "         AS secondary_count "
        "FROM fx "
        "JOIN samples s ON s.accession = fx.accession AND s.run_name = fx.run_name "
        f"WHERE fx.term_id IN ({term_ph}) "
        f"  AND s.submission_year IS NOT NULL AND {where_clause} "
        "GROUP BY s.submission_year, fx.term_id, s.organism_normalized "
        "ORDER BY s.submission_year, fx.term_id"
    )
    params: list[object] = [
        *axis_params,
        *overlay_params,
        *term_ids,
        *where_params,
    ]
    return con.execute(sql, params).fetchdf()


def cohort_breakdown(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    facts_terms: list[tuple[str, str]] | None = None,
    facts_cells: list[list[tuple[str, str]]] | None = None,
) -> pd.DataFrame:
    """(submission_year, organism_normalized, source_system) → sample_count.

    Powers the cohort page's 3-up mini histograms (year / organism / source).
    Aggregates the whole cohort in SQL — the UI's 10K table cap does not
    truncate the histograms.
    """
    where_clause, where_params = _filter_clauses(filters)
    base_params: list[object] = list(where_params)
    extra_clauses: list[str] = []

    term_clauses, term_params = _facts_terms_clauses(facts_terms)
    extra_clauses.extend(term_clauses)
    base_params.extend(term_params)

    cell_clauses, cell_params = _facts_cells_clauses(facts_cells)
    extra_clauses.extend(cell_clauses)
    base_params.extend(cell_params)

    if extra_clauses:
        where_clause = where_clause + " AND " + " AND ".join(extra_clauses)

    sql = (
        "SELECT s.submission_year, s.organism_normalized, s.source_system, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        "       COUNT(DISTINCT CASE WHEN s.source_system LIKE 'chip-atlas-%' "
        "         THEN s.accession END) AS secondary_count "
        "FROM samples s "
        f"WHERE {where_clause} "
        "GROUP BY s.submission_year, s.organism_normalized, s.source_system"
    )
    return con.execute(sql, base_params).fetchdf()


# ---- Fast paths backed by build_aggregates' agg_*.parquet ----
#
# 各関数は対応する agg_*.parquet 経由で集計する。これらは UI cold-start で
# 13.3M facts.parquet を再スキャンするのを避け、~10K 行の agg view を読むだけで
# 同じ結果を返す。agg parquet が未生成 (live deployment 直後など) のときは
# 呼び出し側で fallback (元の live 関数) を選ぶ。
#
# ``_filter_clauses`` は ``samples`` を ``s`` 別名で組み立てるため、agg view 側でも
# ``s`` 別名で参照できるよう FROM 句で ``AS s`` を付ける。


def _has_view(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return bool(row and row[0])


def has_dashboard_aggregates(con: duckdb.DuckDBPyConnection) -> bool:
    """``build-aggregates`` が走っていて、Home/Curation の fast path が使える状態か。"""
    return (
        _has_view(con, "agg_samples_by_dims")
        and _has_view(con, "agg_field_term_dims")
        and _has_view(con, "agg_field_status_dims")
    )


def _agg_filter_clauses(f: SampleFilters) -> tuple[str, list[object]]:
    """``_filter_clauses`` の agg 版 (samples 別名 ``s.`` を付けない)。

    agg_*.parquet には dim 列が inline で乗っており、NULL は ``UNKNOWN``
    リテラルに塗り潰してある (``build_aggregates._build_agg_*``)。よって
    UNKNOWN を含む IN リストは特別扱い不要で素直に IN するだけで live 版と
    同じ集合を返す (docs/data-model.md §"sequence_type の null/mixed/(unknown)
    取扱" の invariant)。
    """
    clauses: list[str] = []
    params: list[object] = []
    if f.organism_normalized:
        placeholders = ",".join(["?"] * len(f.organism_normalized))
        clauses.append(f"organism_normalized IN ({placeholders})")
        params.extend(f.organism_normalized)
    if f.submission_year_min is not None:
        clauses.append("submission_year >= ?")
        params.append(f.submission_year_min)
    if f.submission_year_max is not None:
        clauses.append("submission_year <= ?")
        params.append(f.submission_year_max)
    if f.source_system:
        placeholders = ",".join(["?"] * len(f.source_system))
        clauses.append(f"source_system IN ({placeholders})")
        params.extend(f.source_system)
    if f.sequence_type:
        placeholders = ",".join(["?"] * len(f.sequence_type))
        clauses.append(f"sequence_type IN ({placeholders})")
        params.extend(f.sequence_type)
    if not clauses:
        return "TRUE", []
    return " AND ".join(clauses), params


def samples_by_year_source_fast(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Home F1: 年次積み上げ。agg_samples_by_dims を SUM するだけ。"""
    return con.execute(
        "SELECT submission_year, source_system, "
        "       SUM(sample_count) AS sample_count "
        "FROM agg_samples_by_dims "
        "WHERE submission_year IS NOT NULL "
        "GROUP BY submission_year, source_system "
        "ORDER BY submission_year, source_system"
    ).fetchdf()


def samples_by_organism_fast(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Home F2 donut: organism 別 sample 数。"""
    return con.execute(
        "SELECT organism_normalized, "
        "       SUM(sample_count) AS sample_count "
        "FROM agg_samples_by_dims "
        "GROUP BY organism_normalized "
        "ORDER BY sample_count DESC"
    ).fetchdf()


def samples_by_source_fast(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Home F2 donut: source_system 別 sample 数。"""
    return con.execute(
        "SELECT source_system, "
        "       SUM(sample_count) AS sample_count "
        "FROM agg_samples_by_dims "
        "GROUP BY source_system "
        "ORDER BY sample_count DESC"
    ).fetchdf()


def samples_by_sequence_type_fast(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Home: sequence_type 別 sample 数 (build-aggregates の派生)。"""
    return con.execute(
        "SELECT sequence_type, "
        "       SUM(sample_count) AS sample_count "
        "FROM agg_samples_by_dims "
        "GROUP BY sequence_type "
        "ORDER BY sample_count DESC"
    ).fetchdf()


def field_facts_status_fast(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Home F3/F4: (field, extract_status) → n。"""
    return con.execute(
        "SELECT field, extract_status, SUM(n) AS n "
        "FROM agg_field_status_dims "
        "GROUP BY field, extract_status "
        "ORDER BY field, extract_status"
    ).fetchdf()


def top_terms_overall_fast(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    top_n: int,
) -> pd.DataFrame:
    """Home F5 Pareto: agg_field_term_dims からの SUM TopN。

    ``label`` は agg parquet では最終 ANY_VALUE 済みなので 1 値しか無いはず
    だが、念のため ANY_VALUE を取る。
    """
    _validate_field(field_name)
    return con.execute(
        "SELECT term_id, ANY_VALUE(label) AS label, "
        "       SUM(sample_count) AS sample_count "
        "FROM agg_field_term_dims "
        "WHERE field = ? "
        "GROUP BY term_id "
        "ORDER BY sample_count DESC "
        "LIMIT ?",
        [field_name, top_n],
    ).fetchdf()


def summary_counts_fast(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Home 上部 metric cards: samples / chip_atlas / facts / runs / terms。

    samples / chip_atlas は agg_samples_by_dims から SUM で導出する。
    runs / facts は parquet footer の ``num_rows`` を ``parquet_metadata`` で
    引き、ファイル本体 (とくに 13.3M facts) を scan しない。terms は
    DISTINCT が必要なので live COUNT (ontology は ~5.4M で実用上問題ない)。
    parquet が無い deployment では各 metric を 0 にフォールバックする。
    """
    from bsllmner_viewer.lib.duckdb import default_parquet_dir, parquet_path

    res: dict[str, int] = {}
    row = con.execute(
        "SELECT "
        "  COALESCE(SUM(sample_count), 0), "
        "  COALESCE("
        "    SUM(CASE WHEN source_system LIKE 'chip-atlas-%' THEN sample_count END),"
        "    0) "
        "FROM agg_samples_by_dims"
    ).fetchone()
    res["samples"] = int(row[0]) if row else 0
    res["chip_atlas"] = int(row[1]) if row else 0
    pdir = default_parquet_dir()
    res["runs"] = _parquet_num_rows(con, parquet_path("runs", pdir))
    res["facts"] = _parquet_num_rows(con, parquet_path("facts", pdir))
    res["terms"] = _scalar_count(
        con, "SELECT COUNT(DISTINCT term_id) FROM ontology", "ontology"
    )
    return res


def _scalar_count(
    con: duckdb.DuckDBPyConnection, sql: str, view_name: str
) -> int:
    if not _has_view(con, view_name):
        return 0
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _parquet_num_rows(con: duckdb.DuckDBPyConnection, path: object) -> int:
    """Return ``num_rows`` from a parquet footer without scanning the file.

    Uses pyarrow's ``ParquetFile.metadata.num_rows`` which only reads the
    footer (negligible IO compared to scanning the data). The DuckDB
    connection is unused but kept for symmetry with ``_scalar_count``.
    """
    from pathlib import Path

    import pyarrow.parquet as pq

    if not isinstance(path, Path) or not path.exists():
        return 0
    del con
    parquet_file = pq.ParquetFile(str(path))  # type: ignore[no-untyped-call]
    return int(parquet_file.metadata.num_rows)


def mapping_status_matrix_fast(
    con: duckdb.DuckDBPyConnection, filters: SampleFilters
) -> pd.DataFrame:
    """Curation D1 fast path: agg_field_status_dims を WHERE 絞りで SUM。"""
    where, params = _agg_filter_clauses(filters)
    return con.execute(
        "SELECT field, source_system, extract_status, SUM(n) AS n "
        "FROM agg_field_status_dims "
        f"WHERE {where} "
        "GROUP BY field, source_system, extract_status "
        "ORDER BY field, source_system, extract_status",
        params,
    ).fetchdf()


def mapping_status_over_time_fast(
    con: duckdb.DuckDBPyConnection, filters: SampleFilters
) -> pd.DataFrame:
    """Curation D2 fast path: agg_field_status_dims から (field, year, status) → sum n。"""
    where, params = _agg_filter_clauses(filters)
    return con.execute(
        "SELECT field, submission_year, extract_status, SUM(n) AS n "
        "FROM agg_field_status_dims "
        f"WHERE submission_year IS NOT NULL AND {where} "
        "GROUP BY field, submission_year, extract_status "
        "ORDER BY field, submission_year, extract_status",
        params,
    ).fetchdf()


def top_terms_fast(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int,
) -> list[tuple[str, str]]:
    """``top_terms`` の agg 経由版。``roll_up_depth`` は無視 (leaf レベル top のみ)。

    Gapminder / Home F5 / Gap Discovery の最初の top_terms 呼出を高速化する。
    roll-up が必要な呼出 (Gap Discovery の depth picker) は live ``top_terms``
    側にそのまま流れる前提。
    """
    _validate_field(field_name)
    where, params = _agg_filter_clauses(filters)
    rows = con.execute(
        "SELECT term_id, ANY_VALUE(label) AS lbl, "
        "       SUM(sample_count) AS total "
        "FROM agg_field_term_dims "
        f"WHERE field = ? AND {where} "
        "GROUP BY term_id "
        "ORDER BY total DESC, term_id "
        "LIMIT ?",
        [field_name, *params, top_n],
    ).fetchall()
    return [(str(r[0]), str(r[1]) if r[1] is not None else str(r[0])) for r in rows]


def bubble_dataset_fast(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 30,
) -> pd.DataFrame:
    """``bubble_dataset`` の agg 経由版。

    agg_field_term_dims から top N term を選び、(year, term, organism) で
    SUM して同形の DataFrame を返す。``roll_up_depth`` 未対応 (leaf のみ)。
    live 版と返り値の列名・型を揃える。
    """
    _validate_field(field_name)
    top_pairs = top_terms_fast(con, field_name, filters, top_n)
    if not top_pairs:
        return pd.DataFrame(
            columns=[
                "submission_year",
                "term_id",
                "label",
                "organism_normalized",
                "sample_count",
                "secondary_count",
            ]
        )
    where, params = _agg_filter_clauses(filters)
    term_ids = [t for t, _ in top_pairs]
    term_ph = ",".join(["?"] * len(term_ids))
    return con.execute(
        "SELECT submission_year, term_id, ANY_VALUE(label) AS label, "
        "       organism_normalized, "
        "       SUM(sample_count) AS sample_count, "
        "       SUM(CASE WHEN source_system LIKE 'chip-atlas-%' "
        "         THEN sample_count ELSE 0 END) AS secondary_count "
        "FROM agg_field_term_dims "
        f"WHERE field = ? AND submission_year IS NOT NULL "
        f"  AND term_id IN ({term_ph}) AND {where} "
        "GROUP BY submission_year, term_id, organism_normalized "
        "ORDER BY submission_year, term_id",
        [field_name, *term_ids, *params],
    ).fetchdf()


# ---- Home dashboard helpers (filter-free, full-dataset aggregates) ----


def samples_by_year_source(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """(submission_year, source_system) → sample_count over the full dataset.

    Used by Home F1. Skips NULL submission_year rows (samples missing input
    metadata).
    """
    return con.execute(
        "SELECT submission_year, source_system, "
        "       COUNT(DISTINCT accession) AS sample_count "
        "FROM samples "
        "WHERE submission_year IS NOT NULL "
        "GROUP BY submission_year, source_system "
        "ORDER BY submission_year, source_system"
    ).fetchdf()


def samples_by_organism(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """``organism_normalized`` → sample_count for the Home F2 donut."""
    return con.execute(
        "SELECT COALESCE(organism_normalized, ?) AS organism_normalized, "
        "       COUNT(DISTINCT accession) AS sample_count "
        "FROM samples "
        "GROUP BY 1 "
        "ORDER BY sample_count DESC",
        [UNKNOWN],
    ).fetchdf()


def samples_by_source(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """``source_system`` → sample_count for the Home F2 donut."""
    return con.execute(
        "SELECT source_system, COUNT(DISTINCT accession) AS sample_count "
        "FROM samples "
        "GROUP BY source_system "
        "ORDER BY sample_count DESC"
    ).fetchdf()


def samples_by_sequence_type(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """``sequence_type`` → sample_count for the Home F2 donut.

    NULL is collapsed to ``UNKNOWN`` so the live and fast paths return the
    same slice set under the sentinel invariant.
    """
    return con.execute(
        "SELECT COALESCE(sequence_type, ?) AS sequence_type, "
        "       COUNT(DISTINCT accession) AS sample_count "
        "FROM samples "
        "GROUP BY 1 "
        "ORDER BY sample_count DESC",
        [UNKNOWN],
    ).fetchdf()


def field_facts_status(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """(field, extract_status) → fact row count over the full facts table.

    Used by Home F3 (per-field 100% stacked bar) and F4 (overall metric
    cards). Caller derives ratios in pandas.
    """
    return con.execute(
        "SELECT field, extract_status, COUNT(*) AS n "
        "FROM facts "
        "GROUP BY field, extract_status "
        "ORDER BY field, extract_status"
    ).fetchdf()


def top_terms_overall(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    top_n: int,
) -> pd.DataFrame:
    """Top N (term_id, label, sample_count) for a field, ignoring filters.

    Used by Home F5 (Pareto bar). Returns leaf-level only — depth roll-up is
    out of scope for the dashboard top-list.
    """
    _validate_field(field_name)
    return con.execute(
        "SELECT f.term_id, ANY_VALUE(f.label) AS label, "
        "       COUNT(DISTINCT f.accession) AS sample_count "
        "FROM facts f "
        "WHERE f.field = ? AND f.term_id IS NOT NULL "
        "GROUP BY f.term_id "
        "ORDER BY sample_count DESC "
        "LIMIT ?",
        [field_name, top_n],
    ).fetchdf()


# ---- Curation page helpers ----


def mapping_status_matrix(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
) -> pd.DataFrame:
    """(field, source_system, extract_status) → fact row count, filter-aware.

    Used by Curation D1. Joins facts to samples so sidebar filters propagate.
    """
    where_clause, where_params = _filter_clauses(filters)
    return con.execute(
        "SELECT f.field, s.source_system, f.extract_status, COUNT(*) AS n "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession "
        "  AND s.run_name = f.run_name "
        f"WHERE {where_clause} "
        "GROUP BY f.field, s.source_system, f.extract_status "
        "ORDER BY f.field, s.source_system, f.extract_status",
        where_params,
    ).fetchdf()


def mapping_status_over_time(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
) -> pd.DataFrame:
    """(field, submission_year, extract_status) → fact row count, filter-aware.

    Used by Curation D2. NULL submission_year is dropped so the line chart's x
    axis stays contiguous.
    """
    where_clause, where_params = _filter_clauses(filters)
    return con.execute(
        "SELECT f.field, s.submission_year, f.extract_status, COUNT(*) AS n "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession "
        "  AND s.run_name = f.run_name "
        f"WHERE s.submission_year IS NOT NULL AND {where_clause} "
        "GROUP BY f.field, s.submission_year, f.extract_status "
        "ORDER BY f.field, s.submission_year, f.extract_status",
        where_params,
    ).fetchdf()


def top_unmapped_values(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    top_n: int,
    filters: SampleFilters,
) -> pd.DataFrame:
    """Top N raw ``value`` strings whose mapping failed (term_id IS NULL).

    Used by Curation D3. Returns ``(value, n, sample_count)``: ``n`` is the
    fact-row count and ``sample_count`` the distinct BioSample count — they
    diverge for array-typed fields (drug / knockout_gene / ...).
    """
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    return con.execute(
        "SELECT f.value, COUNT(*) AS n, "
        "       COUNT(DISTINCT f.accession) AS sample_count "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession "
        "  AND s.run_name = f.run_name "
        "WHERE f.field = ? AND f.term_id IS NULL AND f.value IS NOT NULL "
        f"  AND {where_clause} "
        "GROUP BY f.value "
        "ORDER BY n DESC "
        "LIMIT ?",
        [field_name, *where_params, top_n],
    ).fetchdf()


def raw_value_term_flow(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    top_n: int,
    filters: SampleFilters,
    min_count: int = 1,
) -> pd.DataFrame:
    """Top N (value, term_id) pairs whose mapping succeeded — Sankey input.

    Used by Curation D5. ``extract_status = 'ok'`` plus ``term_id IS NOT NULL``
    guarantees we only see resolved mappings; ``value IS NOT NULL`` keeps the
    left side meaningful. ``min_count`` is a frequency floor used to declutter
    the Sankey for high-cardinality fields.
    """
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    return con.execute(
        "SELECT f.value, f.term_id, ANY_VALUE(f.label) AS label, "
        "       COUNT(*) AS n, "
        "       COUNT(DISTINCT f.accession) AS sample_count "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession "
        "  AND s.run_name = f.run_name "
        "WHERE f.field = ? AND f.extract_status = 'ok' "
        "  AND f.term_id IS NOT NULL AND f.value IS NOT NULL "
        f"  AND {where_clause} "
        "GROUP BY f.value, f.term_id "
        "HAVING COUNT(*) >= ? "
        "ORDER BY n DESC "
        "LIMIT ?",
        [field_name, *where_params, min_count, top_n],
    ).fetchdf()


def mixed_bs_srx_composition(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    top_n: int = 20,
) -> pd.DataFrame:
    """SRX-level sequence_type composition of ``mixed`` BioSamples — Curation D7.

    Restricts to BioSamples whose ``samples.sequence_type = 'mixed'`` (under
    ``filters``), joins ``srx_links`` per accession, and counts SRX rows per
    ``(accession, sequence_type)``. Each BioSample's per-type counts are then
    sorted by ``sequence_type`` and serialised into a deterministic pattern
    string (e.g. ``"ATAC-Seq x1 + ChIP-Seq x2"``). Patterns are grouped to
    return the Top N by ``n_bs``.

    Columns: ``pattern`` (tuple-of-(seq, n) JSON-ish string used as the
    grouping key), ``pattern_label`` (human-readable label), ``n_bs``
    (distinct BioSamples carrying the pattern), ``n_srx`` (total SRX rows
    summed across those BioSamples). SRX rows whose ``sequence_type`` is NULL
    are collapsed into ``UNKNOWN`` so they remain visible rather than silently
    dropping the BioSample from the composition view.
    """
    where_clause, where_params = _filter_clauses(filters)
    sql = (
        "WITH bs AS ("
        "  SELECT DISTINCT s.accession FROM samples s "
        f"  WHERE s.sequence_type = 'mixed' AND {where_clause}"
        "), "
        "per_acc_type AS ("
        "  SELECT sl.accession, COALESCE(sl.sequence_type, ?) AS seq, "
        "         COUNT(*) AS n "
        "  FROM srx_links sl "
        "  WHERE sl.accession IN (SELECT accession FROM bs) "
        "  GROUP BY sl.accession, seq"
        "), "
        "per_acc AS ("
        "  SELECT accession, "
        "         STRING_AGG(seq || ' x' || n, ' + ' ORDER BY seq) "
        "           AS pattern_label, "
        "         SUM(n) AS srx_total "
        "  FROM per_acc_type "
        "  GROUP BY accession"
        ") "
        "SELECT pattern_label, "
        "       COUNT(DISTINCT accession) AS n_bs, "
        "       SUM(srx_total) AS n_srx "
        "FROM per_acc "
        "GROUP BY pattern_label "
        "ORDER BY n_bs DESC, n_srx DESC "
        "LIMIT ?"
    )
    df = con.execute(
        sql, [*where_params, UNKNOWN, top_n]
    ).fetchdf()
    cols = ["pattern", "pattern_label", "n_bs", "n_srx"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    # ``pattern`` is a stable machine-friendly key in case callers want to
    # join back; for now it's identical to ``pattern_label`` (SQL already
    # sorted seq alphabetically inside STRING_AGG).
    df["pattern"] = df["pattern_label"]
    df["n_bs"] = df["n_bs"].astype(int)
    df["n_srx"] = df["n_srx"].astype(int)
    return df[cols].reset_index(drop=True)


# ---- Gapminder Tier 2 (Momentum / Diversity / Concentration / Hierarchy) ----


def momentum_dataset(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 15,
    roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Year-over-year momentum data for the Gapminder A6 scatter.

    Aggregates ``bubble_dataset`` over organism so each row is a single
    ``(term_id, year)``, then reindexes every term against the contiguous
    year range and computes per-year ``count_abs`` (level), ``count_delta``
    (year-over-year diff; the first year matches ``count_abs``), and
    ``count_cum`` (running total).
    """
    df = bubble_dataset(con, field_name, filters, top_n, roll_up_depth)
    cols = [
        "term_id",
        "label",
        "submission_year",
        "count_abs",
        "count_delta",
        "count_cum",
    ]
    if df.empty:
        return pd.DataFrame(columns=cols)

    agg = (
        df.groupby(["term_id", "submission_year"], as_index=False)
        .agg(count_abs=("sample_count", "sum"), label=("label", "first"))
    )
    agg["submission_year"] = agg["submission_year"].astype(int)
    years = sorted(agg["submission_year"].unique())
    full_years = list(range(min(years), max(years) + 1))

    label_by_term = (
        agg.drop_duplicates("term_id").set_index("term_id")["label"].to_dict()
    )

    parts: list[pd.DataFrame] = []
    for term_id, g in agg.groupby("term_id", sort=False):
        series = (
            g.set_index("submission_year")["count_abs"]
            .reindex(full_years, fill_value=0)
            .astype(int)
        )
        # diff() returns NaN for the first year; treat the first year's
        # delta as the level itself so a newly-appearing term doesn't render
        # with a NaN momentum (which Plotly silently drops from a scatter).
        delta = series.diff().fillna(series).astype(int)
        cum = series.cumsum().astype(int)
        parts.append(
            pd.DataFrame(
                {
                    "term_id": term_id,
                    "label": label_by_term.get(term_id, term_id),
                    "submission_year": full_years,
                    "count_abs": series.to_numpy(),
                    "count_delta": delta.to_numpy(),
                    "count_cum": cum.to_numpy(),
                }
            )
        )
    return pd.concat(parts, ignore_index=True)[cols]


def cumulative_diversity(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    group_by: str | None = None,
    roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Per-year and cumulative unique-term counts — Gapminder A8.

    ``group_by`` selects the partition column: ``None`` for the overall
    dataset (``group_value = '(overall)'``), ``'organism_normalized'`` or
    ``'source_system'`` to split by that dimension. The cumulative count is
    the size of the running union of ``term_id`` sets and is computed in
    Python because SQL's ``COUNT(DISTINCT)`` doesn't expand into a running
    union without window-level lateral joins that DuckDB doesn't expose.
    """
    _validate_field(field_name)
    allowed_groups = {None, "organism_normalized", "source_system"}
    if group_by not in allowed_groups:
        raise ValueError(f"unknown group_by: {group_by!r}")

    where_clause, where_params = _filter_clauses(filters)
    axis_sql, axis_params = _axis_facts_sql(field_name, roll_up_depth)
    group_expr = (
        f"COALESCE(s.{group_by}, '(unknown)')"
        if group_by
        else "'(overall)'"
    )
    sql = (
        f"WITH fx AS ({axis_sql}) "
        f"SELECT s.submission_year, {group_expr} AS group_value, fx.term_id "
        "FROM fx "
        "JOIN samples s ON s.accession = fx.accession "
        "  AND s.run_name = fx.run_name "
        f"WHERE s.submission_year IS NOT NULL AND {where_clause}"
    )
    raw = con.execute(sql, [*axis_params, *where_params]).fetchdf()
    cols = [
        "submission_year",
        "group_value",
        "unique_terms",
        "cum_unique_terms",
    ]
    if raw.empty:
        return pd.DataFrame(columns=cols)
    raw["submission_year"] = raw["submission_year"].astype(int)

    parts: list[pd.DataFrame] = []
    for grp, g in raw.groupby("group_value", sort=False):
        years = sorted(g["submission_year"].unique())
        full_years = list(range(min(years), max(years) + 1))
        by_year_terms: dict[int, set[str]] = {
            int(y): set(g.loc[g["submission_year"] == y, "term_id"].dropna())
            for y in years
        }
        seen: set[str] = set()
        rows: list[dict[str, object]] = []
        for y in full_years:
            new_terms = by_year_terms.get(y, set())
            seen |= new_terms
            rows.append(
                {
                    "submission_year": y,
                    "group_value": grp,
                    "unique_terms": len(new_terms),
                    "cum_unique_terms": len(seen),
                }
            )
        parts.append(pd.DataFrame(rows))
    return pd.concat(parts, ignore_index=True)[cols]


def _gini(counts: np.ndarray) -> float:
    """Population Gini coefficient. Returns 0 for empty / all-zero input."""
    if counts.size == 0:
        return 0.0
    sorted_c = np.sort(counts)
    total = float(sorted_c.sum())
    if total <= 0.0:
        return 0.0
    n = sorted_c.size
    indices = np.arange(1, n + 1, dtype=float)
    return float(
        (2.0 * float((indices * sorted_c).sum()) - (n + 1) * total)
        / (n * total)
    )


def _shannon_normalized(counts: np.ndarray) -> float:
    """Shannon entropy divided by log(n_terms). Returns 0 for n<=1 or empty."""
    if counts.size <= 1:
        return 0.0
    total = float(counts.sum())
    if total <= 0.0:
        return 0.0
    p = counts / total
    nz = p[p > 0]
    if nz.size <= 1:
        return 0.0
    h = float(-(nz * np.log(nz)).sum())
    return h / float(np.log(counts.size))


def concentration_over_time(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Per-year Gini + max-normalized Shannon entropy — Gapminder A9.

    Both metrics live on [0, 1] so the UI can plot them on a single axis.
    Gini = population formula on per-term sample counts.
    Shannon = entropy / log(n_terms); ``0`` when there's only one term.
    """
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    axis_sql, axis_params = _axis_facts_sql(field_name, roll_up_depth)
    sql = (
        f"WITH fx AS ({axis_sql}) "
        "SELECT s.submission_year, fx.term_id, "
        "       COUNT(DISTINCT s.accession) AS sample_count "
        "FROM fx "
        "JOIN samples s ON s.accession = fx.accession "
        "  AND s.run_name = fx.run_name "
        f"WHERE s.submission_year IS NOT NULL AND {where_clause} "
        "GROUP BY s.submission_year, fx.term_id"
    )
    raw = con.execute(sql, [*axis_params, *where_params]).fetchdf()
    cols = [
        "submission_year",
        "n_terms",
        "total_samples",
        "gini",
        "shannon",
    ]
    if raw.empty:
        return pd.DataFrame(columns=cols)
    raw["submission_year"] = raw["submission_year"].astype(int)

    rows: list[dict[str, object]] = []
    for year, g in raw.groupby("submission_year", sort=True):
        counts = g["sample_count"].astype(float).to_numpy()
        rows.append(
            {
                "submission_year": int(year),
                "n_terms": int(counts.size),
                "total_samples": int(counts.sum()),
                "gini": _gini(counts),
                "shannon": _shannon_normalized(counts),
            }
        )
    return pd.DataFrame(rows)


def term_hierarchy_breakdown(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    root_term: str | None = None,
    max_depth: int = 2,
    roll_up_depth: int | None = None,
    by_year: bool = False,
) -> pd.DataFrame:
    """Ontology-subtree sample counts for Sunburst (B3) / Treemap (A11).

    Returns rows enriched with the direct ``parent_term_id`` (closest
    ancestor in the subtree, i.e. ``parent.depth = term.depth - 1``;
    ``MIN(parent_term_id)`` ties for DAG ambiguity) and ``depth`` from
    ``ontology.parquet``. Only terms with ``sample_count > 0`` are returned
    so empty branches don't bloat the visual. Non-hierarchical fields
    (Cellosaurus / NCBIGene) return an empty DataFrame.
    """
    _validate_field(field_name)
    base_cols = [
        "term_id",
        "parent_term_id",
        "label",
        "depth",
        "sample_count",
        "secondary_count",
    ]
    cols = (["submission_year", *base_cols]) if by_year else base_cols
    if not can_roll_up(field_name):
        return pd.DataFrame(columns=cols)
    source = FIELD_TO_ONTOLOGY[field_name]

    if root_term is not None:
        root_row = con.execute(
            "SELECT depth FROM ontology WHERE term_id = ? "
            "  AND parent_term_id = term_id AND ontology_source = ? LIMIT 1",
            [root_term, source],
        ).fetchone()
        if root_row is None:
            return pd.DataFrame(columns=cols)
        root_depth = int(root_row[0])
        absolute_max = root_depth + max_depth
        subtree_sql = (
            "SELECT DISTINCT o_self.term_id, o_self.label, o_self.depth "
            "FROM ontology o_anc "
            "JOIN ontology o_self "
            "  ON o_self.term_id = o_anc.term_id "
            "  AND o_self.parent_term_id = o_self.term_id "
            "  AND o_self.ontology_source = o_anc.ontology_source "
            "WHERE o_anc.ontology_source = ? AND o_anc.parent_term_id = ? "
            "  AND o_self.depth <= ?"
        )
        subtree = con.execute(
            subtree_sql, [source, root_term, absolute_max]
        ).fetchdf()
    else:
        subtree = con.execute(
            "SELECT term_id, label, depth FROM ontology "
            "WHERE ontology_source = ? AND parent_term_id = term_id "
            "  AND depth <= ?",
            [source, max_depth],
        ).fetchdf()
    if subtree.empty:
        return pd.DataFrame(columns=cols)
    subtree["depth"] = subtree["depth"].astype(int)

    term_ids: list[str] = subtree["term_id"].tolist()
    depth_by_term: dict[str, int] = dict(
        zip(subtree["term_id"], subtree["depth"], strict=False)
    )
    label_by_term: dict[str, str] = dict(
        zip(
            subtree["term_id"],
            subtree["label"].fillna(subtree["term_id"]).astype(str),
            strict=False,
        )
    )

    # Direct parent = ancestor with parent.depth = child.depth - 1. The
    # ontology table holds the transitive closure, so we filter back in
    # Python rather than SELF-JOIN'ing the closure (cheaper for the typical
    # subtree size of <few hundred terms).
    term_ph = ",".join(["?"] * len(term_ids))
    edges = con.execute(
        f"SELECT term_id, parent_term_id FROM ontology "
        f"WHERE ontology_source = ? AND parent_term_id != term_id "
        f"  AND term_id IN ({term_ph})",
        [source, *term_ids],
    ).fetchdf()
    direct_parent: dict[str, str] = {}
    if not edges.empty:
        for child_id, group in edges.groupby("term_id"):
            child_depth = depth_by_term.get(str(child_id))
            if child_depth is None:
                continue
            candidates = [
                str(p)
                for p in group["parent_term_id"]
                if depth_by_term.get(str(p)) == child_depth - 1
            ]
            if candidates:
                direct_parent[str(child_id)] = min(candidates)

    where_clause, where_params = _filter_clauses(filters)
    axis_sql, axis_params = _axis_facts_sql(field_name, roll_up_depth)
    overlay_sql, overlay_params = _overlay_predicate(None)
    if by_year:
        agg_sql = (
            f"WITH fx AS ({axis_sql}) "
            "SELECT fx.term_id, s.submission_year, "
            "       COUNT(DISTINCT s.accession) AS sample_count, "
            f"       COUNT(DISTINCT CASE WHEN {overlay_sql} "
            "         THEN s.accession END) AS secondary_count "
            "FROM fx "
            "JOIN samples s ON s.accession = fx.accession "
            "  AND s.run_name = fx.run_name "
            f"WHERE fx.term_id IN ({term_ph}) "
            f"  AND s.submission_year IS NOT NULL AND {where_clause} "
            "GROUP BY fx.term_id, s.submission_year"
        )
    else:
        agg_sql = (
            f"WITH fx AS ({axis_sql}) "
            "SELECT fx.term_id, "
            "       COUNT(DISTINCT s.accession) AS sample_count, "
            f"       COUNT(DISTINCT CASE WHEN {overlay_sql} "
            "         THEN s.accession END) AS secondary_count "
            "FROM fx "
            "JOIN samples s ON s.accession = fx.accession "
            "  AND s.run_name = fx.run_name "
            f"WHERE fx.term_id IN ({term_ph}) AND {where_clause} "
            "GROUP BY fx.term_id"
        )
    counts = con.execute(
        agg_sql, [*axis_params, *overlay_params, *term_ids, *where_params]
    ).fetchdf()
    if counts.empty:
        return pd.DataFrame(columns=cols)

    counts["parent_term_id"] = counts["term_id"].map(
        lambda t: direct_parent.get(str(t), "")
    )
    counts["label"] = counts["term_id"].map(
        lambda t: label_by_term.get(str(t), str(t))
    )
    counts["depth"] = (
        counts["term_id"].map(lambda t: depth_by_term.get(str(t), 0)).astype(int)
    )
    counts["sample_count"] = counts["sample_count"].astype(int)
    counts["secondary_count"] = counts["secondary_count"].astype(int)
    if by_year:
        counts["submission_year"] = counts["submission_year"].astype(int)
    return counts[cols].reset_index(drop=True)


def field_to_field_flow(
    con: duckdb.DuckDBPyConnection,
    x_field: str,
    y_field: str,
    filters: SampleFilters,
    top_n_x: int = 15,
    top_n_y: int = 15,
    x_roll_up_depth: int | None = None,
    y_roll_up_depth: int | None = None,
) -> pd.DataFrame:
    """Long-form (x, y) flow rows for Gap Discovery B4 Sankey.

    Delegates to ``gap_heatmap_pivot`` (same SQL, same roll-up semantics)
    and keeps only positive-count cells so Plotly Sankey doesn't draw
    zero-thickness links.
    """
    df = gap_heatmap_pivot(
        con,
        x_field,
        y_field,
        filters,
        top_n_x=top_n_x,
        top_n_y=top_n_y,
        x_roll_up_depth=x_roll_up_depth,
        y_roll_up_depth=y_roll_up_depth,
    )
    if df.empty:
        return df
    return df.loc[df["sample_count"] > 0].reset_index(drop=True)


# ---- Cohort C4 (pinned-vs-current cohort comparison) ----


def cohort_overlap_summary(
    accessions_a: list[str], accessions_b: list[str]
) -> dict[str, int]:
    """Three-way set arithmetic for the pinned-vs-current Venn metrics.

    Pure Python set math — no DuckDB dependency. The two inputs are usually
    pre-materialized accession lists (``cohort_samples`` output's
    ``accession`` column), so doing the comparison in-process avoids round-
    tripping the lists through DuckDB twice.
    """
    set_a = set(accessions_a)
    set_b = set(accessions_b)
    return {
        "only_a": len(set_a - set_b),
        "both": len(set_a & set_b),
        "only_b": len(set_b - set_a),
    }


def cohort_term_overlap(
    con: duckdb.DuckDBPyConnection,
    accessions_a: list[str],
    accessions_b: list[str],
    fields: list[str] | None = None,
) -> pd.DataFrame:
    """Per-field Jaccard overlap of term sets between two cohorts.

    For each field, computes the distinct ``term_id`` set within each
    cohort's accession list and reports cardinalities + Jaccard
    (``|A∩B| / |A∪B|``). Used by Cohort C4 to surface "where does the
    composition differ?" (low Jaccard) vs "same shape, different size"
    (high Jaccard).
    """
    target_fields = list(fields) if fields else list(VALID_FIELDS)
    for f in target_fields:
        _validate_field(f)
    out_cols = ["field", "n_pinned", "n_current", "n_both", "jaccard"]
    if not accessions_a and not accessions_b:
        return pd.DataFrame(columns=out_cols)

    acc_a = sorted(set(accessions_a))
    acc_b = sorted(set(accessions_b))

    def _terms_for(field_name: str, accs: list[str]) -> set[str]:
        if not accs:
            return set()
        rows = con.execute(
            "SELECT DISTINCT term_id FROM facts "
            "WHERE field = ? AND term_id IS NOT NULL "
            "  AND accession IN (SELECT UNNEST(?::VARCHAR[]))",
            [field_name, accs],
        ).fetchall()
        return {str(r[0]) for r in rows}

    rows_out: list[dict[str, object]] = []
    for fld in target_fields:
        terms_a = _terms_for(fld, acc_a)
        terms_b = _terms_for(fld, acc_b)
        union = terms_a | terms_b
        intersect = terms_a & terms_b
        jaccard = (len(intersect) / len(union)) if union else 0.0
        rows_out.append(
            {
                "field": fld,
                "n_pinned": len(terms_a),
                "n_current": len(terms_b),
                "n_both": len(intersect),
                "jaccard": float(jaccard),
            }
        )
    return pd.DataFrame(rows_out, columns=out_cols)
