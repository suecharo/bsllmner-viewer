"""ontology.parquet helper queries.

The ontology table contains transitive closure rows plus self-loops:
each term has a (term_id, parent_term_id = term_id) row so that
"include self in subtree query" reduces to a simple equality filter.

Cellosaurus is a documented special case: it has no `rdfs:subClassOf`
is-a edges (all `subClassOf` are `owl:Restriction` semantic relations),
so every term is self-loop-only. `descendants("CVCL:...")` therefore
returns just the term itself.
"""

from __future__ import annotations

import duckdb


def label(con: duckdb.DuckDBPyConnection, term_id: str) -> str | None:
    row = con.execute(
        "SELECT label FROM ontology "
        "WHERE term_id = ? AND parent_term_id = term_id LIMIT 1",
        [term_id],
    ).fetchone()
    return row[0] if row else None


def descendants(
    con: duckdb.DuckDBPyConnection, term_id: str, source: str
) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT term_id FROM ontology "
        "WHERE parent_term_id = ? AND ontology_source = ? "
        "ORDER BY term_id",
        [term_id, source],
    ).fetchall()
    return [r[0] for r in rows]


def ancestors(
    con: duckdb.DuckDBPyConnection, term_id: str, source: str
) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT parent_term_id FROM ontology "
        "WHERE term_id = ? AND ontology_source = ? "
        "ORDER BY parent_term_id",
        [term_id, source],
    ).fetchall()
    return [r[0] for r in rows]


def roots(con: duckdb.DuckDBPyConnection, source: str) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT term_id FROM ontology "
        "WHERE ontology_source = ? AND depth = 0 "
        "ORDER BY term_id",
        [source],
    ).fetchall()
    return [r[0] for r in rows]


def terms_at_depth(
    con: duckdb.DuckDBPyConnection, source: str, depth: int
) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT term_id FROM ontology "
        "WHERE ontology_source = ? AND depth = ? "
        "ORDER BY term_id",
        [source, depth],
    ).fetchall()
    return [r[0] for r in rows]
