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

from dataclasses import dataclass

import duckdb


@dataclass(frozen=True)
class TermSummary:
    """Single-row view of an ontology term.

    All ontology-derived fields are nullable because the term may not be in
    `ontology.parquet` (e.g. NCBI Gene IDs are referenced from facts but not
    materialized in ontology.parquet, see `docs/data-model.md`).
    """

    term_id: str
    label: str | None
    ontology_source: str | None
    depth: int | None


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


def term_summary(
    con: duckdb.DuckDBPyConnection, term_id: str
) -> TermSummary:
    """Return label / ontology_source / depth for a term (None if absent)."""
    row = con.execute(
        "SELECT label, ontology_source, depth FROM ontology "
        "WHERE term_id = ? AND parent_term_id = term_id LIMIT 1",
        [term_id],
    ).fetchone()
    if row is None:
        return TermSummary(
            term_id=term_id, label=None, ontology_source=None, depth=None
        )
    label_value, source_value, depth_value = row
    return TermSummary(
        term_id=term_id,
        label=label_value,
        ontology_source=source_value,
        depth=int(depth_value) if depth_value is not None else None,
    )


def term_summaries(
    con: duckdb.DuckDBPyConnection,
    term_ids: list[str],
) -> dict[str, TermSummary]:
    """Batch version of ``term_summary`` for a list of term IDs.

    UI pages render many term popovers per rerun (Top N axis terms on Gap
    Discovery, all cohort constraints on Cohort, etc.). Calling
    ``term_summary`` per term forces an ontology.parquet scan each time —
    1 SQL with ``UNNEST(?::VARCHAR[])`` collapses that into a single scan.

    Term IDs absent from ``ontology.parquet`` (e.g. NCBI Gene IDs) still
    appear in the returned dict with all fields = None, matching the
    fallback behaviour of ``term_summary``.
    """
    if not term_ids:
        return {}
    rows = con.execute(
        "SELECT term_id, label, ontology_source, depth FROM ontology "
        "WHERE term_id IN (SELECT UNNEST(?::VARCHAR[])) "
        "  AND parent_term_id = term_id",
        [term_ids],
    ).fetchall()
    out: dict[str, TermSummary] = {
        tid: TermSummary(term_id=tid, label=None, ontology_source=None, depth=None)
        for tid in term_ids
    }
    for tid, label_value, source_value, depth_value in rows:
        out[tid] = TermSummary(
            term_id=tid,
            label=label_value,
            ontology_source=source_value,
            depth=int(depth_value) if depth_value is not None else None,
        )
    return out


# Per-prefix URL builders. Each (site, builder) entry returns the user-facing
# label and the absolute URL for the term. New ontologies extend this table —
# `external_url` itself is just a dispatch over the term_id prefix.
def external_url(term_id: str) -> tuple[str, str] | None:
    """Build the official ontology page URL for a term_id.

    Returns ``(site_label, url)`` on a known prefix, else ``None``.
    """
    prefix, _, local = term_id.partition(":")
    if not local:
        return None
    if prefix == "MONDO":
        return ("Monarch Initiative", f"https://monarchinitiative.org/disease/MONDO:{local}")
    if prefix == "CL":
        iri = f"http%3A%2F%2Fpurl.obolibrary.org%2Fobo%2FCL_{local}"
        return ("EBI OLS (CL)", f"https://www.ebi.ac.uk/ols4/ontologies/cl/classes/{iri}")
    if prefix == "UBERON":
        iri = f"http%3A%2F%2Fpurl.obolibrary.org%2Fobo%2FUBERON_{local}"
        return ("EBI OLS (UBERON)", f"https://www.ebi.ac.uk/ols4/ontologies/uberon/classes/{iri}")
    if prefix == "CHEBI":
        return ("EBI ChEBI", f"https://www.ebi.ac.uk/chebi/searchId.do?chebiId=CHEBI:{local}")
    if prefix == "CVCL":
        return ("Cellosaurus", f"https://www.cellosaurus.org/CVCL_{local}")
    if prefix == "NCBIGene":
        return ("NCBI Gene", f"https://www.ncbi.nlm.nih.gov/gene/{local}")
    return None
