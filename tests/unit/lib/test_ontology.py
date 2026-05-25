from __future__ import annotations

from pathlib import Path

from bsllmner_viewer.lib.duckdb import get_conn
from bsllmner_viewer.lib.ontology import (
    ancestors,
    descendants,
    label,
    roots,
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
