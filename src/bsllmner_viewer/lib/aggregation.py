"""Heatmap and bubble aggregation queries against samples + facts parquet."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import duckdb
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


def can_roll_up(field_name: str) -> bool:
    return field_name in FIELD_TO_ONTOLOGY


@dataclass
class SampleFilters:
    organism_normalized: list[str] = field(default_factory=list)
    submission_year_min: int | None = None
    submission_year_max: int | None = None
    source_system: list[str] = field(default_factory=list)
    in_chip_atlas: bool | None = None


def _filter_clauses(f: SampleFilters) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if f.organism_normalized:
        placeholders = ",".join(["?"] * len(f.organism_normalized))
        clauses.append(f"s.organism_normalized IN ({placeholders})")
        params.extend(f.organism_normalized)
    if f.submission_year_min is not None:
        clauses.append("s.submission_year >= ?")
        params.append(f.submission_year_min)
    if f.submission_year_max is not None:
        clauses.append("s.submission_year <= ?")
        params.append(f.submission_year_max)
    if f.source_system:
        placeholders = ",".join(["?"] * len(f.source_system))
        clauses.append(f"s.source_system IN ({placeholders})")
        params.extend(f.source_system)
    if f.in_chip_atlas is not None:
        clauses.append("s.in_chip_atlas = ?")
        params.append(f.in_chip_atlas)
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
) -> pd.DataFrame:
    """Return a long-form DataFrame of (x_term, y_term) sample counts.

    Columns: ``x_term_id``, ``x_label``, ``y_term_id``, ``y_label``,
    ``sample_count``, ``chip_atlas_count``.

    ``x_roll_up_depth`` / ``y_roll_up_depth``: when set, replace each leaf
    term with ``MIN(parent_term_id)`` at that depth in the field's primary
    ontology (see ``FIELD_TO_ONTOLOGY``). Fields outside ``FIELD_TO_ONTOLOGY``
    or with no eligible ancestor fall back to the leaf term.

    Only cells with sample_count > 0 are returned; the caller pivots and
    reindexes against the chosen axis term lists to surface empty cells.
    """
    _validate_field(x_field)
    _validate_field(y_field)

    x_pairs = top_terms(con, x_field, filters, top_n_x, x_roll_up_depth)
    y_pairs = top_terms(con, y_field, filters, top_n_y, y_roll_up_depth)
    if not x_pairs or not y_pairs:
        return pd.DataFrame(
            columns=[
                "x_term_id",
                "x_label",
                "y_term_id",
                "y_label",
                "sample_count",
                "chip_atlas_count",
            ]
        )

    where_clause, where_params = _filter_clauses(filters)
    x_axis_sql, x_axis_params = _axis_facts_sql(x_field, x_roll_up_depth)
    y_axis_sql, y_axis_params = _axis_facts_sql(y_field, y_roll_up_depth)
    x_terms = [t for t, _ in x_pairs]
    y_terms = [t for t, _ in y_pairs]
    x_ph = ",".join(["?"] * len(x_terms))
    y_ph = ",".join(["?"] * len(y_terms))

    sql = (
        f"WITH fx AS ({x_axis_sql}), fy AS ({y_axis_sql}) "
        "SELECT fx.term_id AS x_term_id, ANY_VALUE(fx.label) AS x_label, "
        "       fy.term_id AS y_term_id, ANY_VALUE(fy.label) AS y_label, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        "       COUNT(DISTINCT CASE WHEN s.in_chip_atlas THEN s.accession END) AS chip_atlas_count "
        "FROM fx "
        "JOIN fy ON fx.accession = fy.accession AND fx.run_name = fy.run_name "
        "JOIN samples s ON s.accession = fx.accession AND s.run_name = fx.run_name "
        f"WHERE fx.term_id IN ({x_ph}) AND fy.term_id IN ({y_ph}) AND {where_clause} "
        "GROUP BY fx.term_id, fy.term_id"
    )
    params: list[object] = [
        *x_axis_params,
        *y_axis_params,
        *x_terms,
        *y_terms,
        *where_params,
    ]
    df = con.execute(sql, params).fetchdf()
    return df


def cohort_samples(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    facts_terms: list[tuple[str, str]] | None = None,
    limit: int = 10000,
) -> pd.DataFrame:
    """Return matching samples for a cohort.

    ``facts_terms`` is a list of (field, term_id) pairs that the sample must
    have. All pairs must match (AND semantics).
    """
    where_clause, where_params = _filter_clauses(filters)
    base_params: list[object] = list(where_params)

    if facts_terms:
        for field_name, _ in facts_terms:
            _validate_field(field_name)
        clauses = []
        for field_name, term_id in facts_terms:
            clauses.append(
                "EXISTS (SELECT 1 FROM facts f WHERE f.accession = s.accession "
                "AND f.run_name = s.run_name AND f.field = ? AND f.term_id = ?)"
            )
            base_params.extend([field_name, term_id])
        where_clause = where_clause + " AND " + " AND ".join(clauses)

    sql = (
        "SELECT s.accession, s.organism_normalized, s.submission_year, s.project, "
        "       s.title, s.source_system, s.in_chip_atlas, s.chip_atlas_genome "
        "FROM samples s "
        f"WHERE {where_clause} "
        "ORDER BY s.submission_year DESC, s.accession "
        "LIMIT ?"
    )
    return con.execute(sql, [*base_params, limit]).fetchdf()


def cohort_count(
    con: duckdb.DuckDBPyConnection,
    filters: SampleFilters,
    facts_terms: list[tuple[str, str]] | None = None,
) -> int:
    where_clause, where_params = _filter_clauses(filters)
    base_params: list[object] = list(where_params)
    if facts_terms:
        for field_name, _ in facts_terms:
            _validate_field(field_name)
        clauses = []
        for field_name, term_id in facts_terms:
            clauses.append(
                "EXISTS (SELECT 1 FROM facts f WHERE f.accession = s.accession "
                "AND f.run_name = s.run_name AND f.field = ? AND f.term_id = ?)"
            )
            base_params.extend([field_name, term_id])
        where_clause = where_clause + " AND " + " AND ".join(clauses)
    sql = f"SELECT COUNT(DISTINCT s.accession) FROM samples s WHERE {where_clause}"
    row = con.execute(sql, base_params).fetchone()
    return int(row[0]) if row else 0


def cumulative_bubble_dataset(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 15,
) -> pd.DataFrame:
    """Same as ``bubble_dataset`` but with per-year counts cumulatively summed.

    Each ``(term_id, organism_normalized)`` group is reindexed against the full
    contiguous year range present in the underlying data and filled with 0
    where no rows exist, then ``cumsum`` is applied. Cumulative columns are
    ``sample_count_cum`` / ``chip_atlas_count_cum``; the original
    ``sample_count`` / ``chip_atlas_count`` per-year values are kept.
    """
    df = bubble_dataset(con, field_name, filters, top_n)
    cum_columns = ["sample_count_cum", "chip_atlas_count_cum"]
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
            g[["submission_year", "sample_count", "chip_atlas_count"]]
            .set_index("submission_year")
            .reindex(full_years, fill_value=0)
        )
        sub["term_id"] = term_id
        sub["organism_normalized"] = org
        sub["label"] = term_labels.get(term_id, term_id)
        sub["sample_count_cum"] = sub["sample_count"].cumsum()
        sub["chip_atlas_count_cum"] = sub["chip_atlas_count"].cumsum()
        parts.append(sub.reset_index())

    return pd.concat(parts, ignore_index=True)


def bubble_dataset(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int = 30,
) -> pd.DataFrame:
    """Aggregate (year, term) → sample count, with chip-atlas overlay.

    Returns columns: ``submission_year``, ``term_id``, ``label``,
    ``sample_count``, ``chip_atlas_count``, ``organism_normalized``.
    """
    _validate_field(field_name)
    top_pairs = top_terms(con, field_name, filters, top_n)
    if not top_pairs:
        return pd.DataFrame(
            columns=[
                "submission_year",
                "term_id",
                "label",
                "organism_normalized",
                "sample_count",
                "chip_atlas_count",
            ]
        )
    where_clause, where_params = _filter_clauses(filters)
    term_ids = [t for t, _ in top_pairs]
    term_ph = ",".join(["?"] * len(term_ids))
    sql = (
        "SELECT s.submission_year, f.term_id, ANY_VALUE(f.label) AS label, "
        "       s.organism_normalized, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        "       COUNT(DISTINCT CASE WHEN s.in_chip_atlas THEN s.accession END) AS chip_atlas_count "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession AND s.run_name = f.run_name "
        f"WHERE f.field = ? AND f.term_id IN ({term_ph}) "
        f"  AND s.submission_year IS NOT NULL AND {where_clause} "
        "GROUP BY s.submission_year, f.term_id, s.organism_normalized "
        "ORDER BY s.submission_year, f.term_id"
    )
    params = [field_name, *term_ids, *where_params]
    return con.execute(sql, params).fetchdf()
