from __future__ import annotations

import os
from pathlib import Path

import duckdb

_PARQUET_NAMES = ("samples", "facts", "runs", "ontology", "srx_links")

# srx_links.parquet 不在時のフォールバック。aggregation 側が常に srx_links を
# 参照できるよう、空の VIEW を同じ schema で作っておく。
_EMPTY_SRX_LINKS_VIEW = (
    "CREATE VIEW srx_links AS SELECT "
    "NULL::VARCHAR AS srx, "
    "NULL::VARCHAR AS accession, "
    "NULL::VARCHAR AS bioproject, "
    "NULL::VARCHAR AS sra_study, "
    "NULL::VARCHAR AS sra_sample, "
    "NULL::VARCHAR AS status "
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
