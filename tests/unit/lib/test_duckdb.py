from __future__ import annotations

from pathlib import Path

from bsllmner_viewer.lib.duckdb import get_conn


def test_get_conn_creates_view_for_existing_parquet(fixture_parquet_dir: Path) -> None:
    con = get_conn(parquet_dir=fixture_parquet_dir)
    n = con.execute("SELECT COUNT(*) FROM ontology").fetchone()
    assert n is not None
    assert n[0] == 10


def test_get_conn_skips_missing_parquet(fixture_parquet_dir: Path) -> None:
    # samples.parquet doesn't exist in fixture -> no view created
    con = get_conn(parquet_dir=fixture_parquet_dir)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    assert tables == {"ontology"}


def test_get_conn_empty_dir(tmp_path: Path) -> None:
    pdir = tmp_path / "empty"
    pdir.mkdir()
    con = get_conn(parquet_dir=pdir)
    tables = con.execute("SHOW TABLES").fetchall()
    assert tables == []
