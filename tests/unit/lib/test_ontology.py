from __future__ import annotations

from pathlib import Path

from bsllmner_viewer.lib.duckdb import get_conn
from bsllmner_viewer.lib.ontology import (
    ancestors,
    descendants,
    external_url,
    label,
    roots,
    term_summary,
    terms_at_depth,
)


def test_label_existing(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert label(con, "T:1") == "root"
    assert label(con, "T:3") == "grandchild"
    assert label(con, "C:1") == "c1"


def test_label_missing(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert label(con, "DOES:NOT_EXIST") is None


def test_descendants_root_returns_subtree_including_self(
    fixture_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert descendants(con, "T:1", "TEST") == ["T:1", "T:2", "T:3", "T:4"]


def test_descendants_leaf_returns_only_self(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert descendants(con, "T:3", "TEST") == ["T:3"]


def test_descendants_cellosaurus_like_self_loop_only(
    fixture_parquet_dir: Path,
) -> None:
    # Cellosaurus has no parent edges by design: descendants is just the term.
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert descendants(con, "C:1", "CELL") == ["C:1"]


def test_descendants_source_isolation(fixture_parquet_dir: Path) -> None:
    # T:1 belongs to TEST, not TEST2.
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert descendants(con, "T:1", "TEST2") == []


def test_ancestors_includes_self_and_transitive_parents(
    fixture_parquet_dir: Path,
) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert ancestors(con, "T:3", "TEST") == ["T:1", "T:2", "T:3"]


def test_ancestors_root_returns_only_self(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert ancestors(con, "T:1", "TEST") == ["T:1"]


def test_ancestors_source_isolation(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert ancestors(con, "T:3", "TEST2") == []


def test_roots(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert roots(con, "TEST") == ["T:1"]
    assert roots(con, "TEST2") == ["U:1"]
    assert roots(con, "CELL") == ["C:1"]


def test_terms_at_depth(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    assert terms_at_depth(con, "TEST", 0) == ["T:1"]
    assert terms_at_depth(con, "TEST", 1) == ["T:2", "T:4"]
    assert terms_at_depth(con, "TEST", 2) == ["T:3"]
    assert terms_at_depth(con, "TEST", 3) == []


# ---- term_summary ----


def test_term_summary_hit(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    s = term_summary(con, "T:3")
    assert s.term_id == "T:3"
    assert s.label == "grandchild"
    assert s.ontology_source == "TEST"
    assert s.depth == 2


def test_term_summary_root(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    s = term_summary(con, "T:1")
    assert s.ontology_source == "TEST"
    assert s.depth == 0


def test_term_summary_missing(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    s = term_summary(con, "DOES:NOT_EXIST")
    assert s.term_id == "DOES:NOT_EXIST"
    assert s.label is None
    assert s.ontology_source is None
    assert s.depth is None


# ---- external_url ----


def test_external_url_mondo() -> None:
    site, url = external_url("MONDO:0005061") or (None, None)
    assert site == "Monarch Initiative"
    assert url == "https://monarchinitiative.org/disease/MONDO:0005061"


def test_external_url_cl_uses_ols_encoded_iri() -> None:
    result = external_url("CL:0000000")
    assert result is not None
    site, url = result
    assert site == "EBI OLS (CL)"
    assert url == (
        "https://www.ebi.ac.uk/ols4/ontologies/cl/classes/"
        "http%3A%2F%2Fpurl.obolibrary.org%2Fobo%2FCL_0000000"
    )


def test_external_url_uberon_uses_ols_encoded_iri() -> None:
    result = external_url("UBERON:0000948")
    assert result is not None
    _, url = result
    assert "ols4/ontologies/uberon/classes/" in url
    assert url.endswith("UBERON_0000948")


def test_external_url_chebi_passes_full_id() -> None:
    result = external_url("CHEBI:15377")
    assert result is not None
    site, url = result
    assert site == "EBI ChEBI"
    assert url == "https://www.ebi.ac.uk/chebi/searchId.do?chebiId=CHEBI:15377"


def test_external_url_cvcl_uses_underscore() -> None:
    result = external_url("CVCL:0030")
    assert result is not None
    site, url = result
    assert site == "Cellosaurus"
    assert url == "https://www.cellosaurus.org/CVCL_0030"


def test_external_url_ncbigene_strips_prefix() -> None:
    result = external_url("NCBIGene:7157")
    assert result is not None
    site, url = result
    assert site == "NCBI Gene"
    assert url == "https://www.ncbi.nlm.nih.gov/gene/7157"


def test_external_url_unknown_prefix_returns_none() -> None:
    assert external_url("WEIRD:1234") is None


def test_external_url_malformed_returns_none() -> None:
    # No colon at all → no local part → not a routable term_id.
    assert external_url("MONDO0005061") is None
