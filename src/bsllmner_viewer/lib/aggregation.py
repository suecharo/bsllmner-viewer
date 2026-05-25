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


def top_terms(
    con: duckdb.DuckDBPyConnection,
    field_name: str,
    filters: SampleFilters,
    top_n: int,
) -> list[tuple[str, str]]:
    _validate_field(field_name)
    where_clause, where_params = _filter_clauses(filters)
    rows = con.execute(
        "SELECT f.term_id, ANY_VALUE(f.label) AS lbl "
        "FROM facts f "
        "JOIN samples s ON s.accession = f.accession AND s.run_name = f.run_name "
        f"WHERE f.field = ? AND f.term_id IS NOT NULL AND {where_clause} "
        "GROUP BY f.term_id "
        "ORDER BY COUNT(DISTINCT s.accession) DESC "
        "LIMIT ?",
        [field_name, *where_params, top_n],
    ).fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def gap_heatmap_pivot(
    con: duckdb.DuckDBPyConnection,
    x_field: str,
    y_field: str,
    filters: SampleFilters,
    top_n_x: int = 30,
    top_n_y: int = 30,
) -> pd.DataFrame:
    """Return a long-form DataFrame of (x_term, y_term) sample counts.

    Columns: ``x_term_id``, ``x_label``, ``y_term_id``, ``y_label``,
    ``sample_count``, ``chip_atlas_count``.

    Only cells with sample_count > 0 are returned; the caller pivots and
    reindexes against the chosen axis term lists to surface empty cells.
    """
    _validate_field(x_field)
    _validate_field(y_field)

    x_pairs = top_terms(con, x_field, filters, top_n_x)
    y_pairs = top_terms(con, y_field, filters, top_n_y)
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
    x_terms = [t for t, _ in x_pairs]
    y_terms = [t for t, _ in y_pairs]
    x_ph = ",".join(["?"] * len(x_terms))
    y_ph = ",".join(["?"] * len(y_terms))

    sql = (
        "WITH fx AS ("
        "  SELECT DISTINCT f.accession, f.run_name, f.term_id AS x_term, f.label AS x_label "
        "  FROM facts f "
        "  JOIN samples s ON s.accession = f.accession AND s.run_name = f.run_name "
        f" WHERE f.field = ? AND f.term_id IN ({x_ph}) AND {where_clause}"
        "), fy AS ("
        "  SELECT DISTINCT f.accession, f.run_name, f.term_id AS y_term, f.label AS y_label "
        "  FROM facts f "
        "  JOIN samples s ON s.accession = f.accession AND s.run_name = f.run_name "
        f" WHERE f.field = ? AND f.term_id IN ({y_ph}) AND {where_clause}"
        ") "
        "SELECT fx.x_term AS x_term_id, ANY_VALUE(fx.x_label) AS x_label, "
        "       fy.y_term AS y_term_id, ANY_VALUE(fy.y_label) AS y_label, "
        "       COUNT(DISTINCT s.accession) AS sample_count, "
        "       COUNT(DISTINCT CASE WHEN s.in_chip_atlas THEN s.accession END) AS chip_atlas_count "
        "FROM fx "
        "JOIN fy ON fx.accession = fy.accession AND fx.run_name = fy.run_name "
        "JOIN samples s ON s.accession = fx.accession AND s.run_name = fx.run_name "
        "GROUP BY fx.x_term, fy.y_term"
    )
    params = [x_field, *x_terms, *where_params, y_field, *y_terms, *where_params]
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
