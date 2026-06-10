from __future__ import annotations

import os
from pathlib import Path

import duckdb

# `samples`/`facts`/`runs`/`ontology`/`srx_links` は SSOT。`agg_*` は ETL の
# `build-aggregates` 出力で、samples/facts を再スキャンせず Home / Gapminder /
# Curation の cold-start クエリを高速化する用途。agg_* parquet が無くても view
# は作らず、UI 側で各 helper が fallback (live aggregation) する。
_PARQUET_NAMES = (
    "samples",
    "facts",
    "runs",
    "ontology",
    "srx_links",
    "agg_samples_by_dims",
    "agg_field_term_dims",
    "agg_field_status_dims",
)

# srx_links.parquet 不在時のフォールバック。aggregation 側が常に srx_links を
# 参照できるよう、空の VIEW を同じ schema で作っておく。
_EMPTY_SRX_LINKS_VIEW = (
    "CREATE VIEW srx_links AS SELECT "
    "NULL::VARCHAR AS srx, "
    "NULL::VARCHAR AS accession, "
    "NULL::VARCHAR AS bioproject, "
    "NULL::VARCHAR AS sra_study, "
    "NULL::VARCHAR AS sra_sample, "
    "NULL::VARCHAR AS status, "
    "NULL::VARCHAR AS sequence_type "
    "WHERE FALSE"
)


def default_parquet_dir() -> Path:
    return Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data")) / "parquet"


def get_conn(parquet_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    pdir = parquet_dir if parquet_dir is not None else default_parquet_dir()
    con = duckdb.connect(database=":memory:")
    for name in _PARQUET_NAMES:
        path = pdir / f"{name}.parquet"
        if path.exists():
            escaped = str(path).replace("'", "''")
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{escaped}')"
            )
        elif name == "srx_links":
            con.execute(_EMPTY_SRX_LINKS_VIEW)
    return con


def has_view(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """指定名の VIEW (= parquet が存在して登録された) が居るかを返す。"""
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = ? AND table_schema = 'main'",
        [name],
    ).fetchone()
    return bool(row and row[0])
