"""samples.parquet + facts.parquet から小さい pre-aggregated parquet 群を出力する。

Streamlit UI の cold-start で 13.3M 行の facts.parquet を 5〜10 個の query が
スキャンしている (Gap Discovery で 3 facts scan + 3-way join、Curation で full
facts JOIN を 2 回、Home F3/F5 でも facts スキャン)。これらの query を ETL で
事前集計し、UI 側は数十 KB の agg_*.parquet を読むだけにする。

cardinality 上限:

| ファイル                       | row 上限 (実測 << 上限)                                |
|-------------------------------|-------------------------------------------------------|
| agg_samples_by_dims.parquet   | 30 yr × 3 src × 8 seq × 6 org × 2 chip ≈ 8.6K         |
| agg_field_term_dims.parquet   | top 200/field × yr × src × seq × org × chip × 8 fields, sparse |
| agg_field_status_dims.parquet | 8 field × 3 src × 8 seq × 30 yr × 3 status ≈ 17K      |

実 data でも数 MB ずつに収まり、Streamlit cold-start は数十 ms オーダになる。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import duckdb

logger = logging.getLogger(__name__)

# 各 field の上位 N term を持っておけば top_terms / bubble_dataset / momentum 等の
# 入口 query は agg parquet 1 個で完結する。実 data の disease/drug が ~1〜5K
# 種類なので 200 で十分カバーされる。
_TOP_TERMS_PER_FIELD: Final[int] = 200


def _parquet_columns(con: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
    rows = con.execute(
        "SELECT name FROM parquet_schema(?) WHERE name IS NOT NULL",
        [str(path)],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _build_agg_samples(con: duckdb.DuckDBPyConnection, out_path: Path) -> None:
    """(submission_year, source_system, sequence_type, organism_normalized,
    in_chip_atlas) → sample_count。

    Home F1/F2/F4、cohort_breakdown (no facts predicate)、samples_by_*、
    cohort 画面の mini histogram の base になる。
    """
    sql = (
        "COPY ("
        "  SELECT "
        "    submission_year, "
        "    source_system, "
        "    COALESCE(sequence_type, '(unknown)') AS sequence_type, "
        "    COALESCE(organism_normalized, '(unknown)') AS organism_normalized, "
        "    in_chip_atlas, "
        "    COUNT(DISTINCT accession) AS sample_count "
        "  FROM samples "
        "  GROUP BY 1, 2, 3, 4, 5"
        f") TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    con.execute(sql)
    rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()
    logger.info("wrote %s (%d rows)", out_path, int(rows[0]) if rows else 0)


def _build_agg_field_term_dims(con: duckdb.DuckDBPyConnection, out_path: Path) -> None:
    """(field, term_id, label, submission_year, source_system, sequence_type,
    organism_normalized, in_chip_atlas) → sample_count。

    各 field で sample_count 上位 N term だけ残す。bubble_dataset /
    cumulative_bubble_dataset / momentum_dataset / cumulative_diversity /
    concentration_over_time / Home F5 Pareto / top_terms / top_terms_overall
    の出発点を agg 1 個に集約する。
    """
    sql = (
        "WITH joined AS ("
        "  SELECT f.field, f.term_id, f.label, "
        "         s.submission_year, s.source_system, "
        "         COALESCE(s.sequence_type, '(unknown)') AS sequence_type, "
        "         COALESCE(s.organism_normalized, '(unknown)') AS organism_normalized, "
        "         s.in_chip_atlas, s.accession "
        "  FROM facts f "
        "  JOIN samples s ON s.accession = f.accession "
        "    AND s.run_name = f.run_name "
        "  WHERE f.term_id IS NOT NULL "
        "), term_totals AS ("
        "  SELECT field, term_id, "
        "         COUNT(DISTINCT accession) AS total_samples "
        "  FROM joined "
        "  GROUP BY field, term_id "
        "), ranked AS ("
        "  SELECT field, term_id, total_samples, "
        "         row_number() OVER (PARTITION BY field "
        "                            ORDER BY total_samples DESC, term_id ASC) AS rk "
        "  FROM term_totals "
        "), kept AS ("
        "  SELECT field, term_id FROM ranked WHERE rk <= ?"
        ") "
        "SELECT j.field, j.term_id, ANY_VALUE(j.label) AS label, "
        "       j.submission_year, j.source_system, j.sequence_type, "
        "       j.organism_normalized, j.in_chip_atlas, "
        "       COUNT(DISTINCT j.accession) AS sample_count "
        "FROM joined j "
        "JOIN kept k ON k.field = j.field AND k.term_id = j.term_id "
        "GROUP BY j.field, j.term_id, j.submission_year, j.source_system, "
        "         j.sequence_type, j.organism_normalized, j.in_chip_atlas"
    )
    df = con.execute(sql, [_TOP_TERMS_PER_FIELD]).fetchdf()
    df.to_parquet(out_path, compression="zstd", index=False)
    logger.info(
        "wrote %s (%d rows, top %d terms/field)",
        out_path,
        len(df),
        _TOP_TERMS_PER_FIELD,
    )


def _build_agg_field_status(con: duckdb.DuckDBPyConnection, out_path: Path) -> None:
    """(field, source_system, sequence_type, submission_year, organism_normalized,
    in_chip_atlas, extract_status) → n。

    Home F3/F4 + Curation D1 (status × source matrix) + D2 (status × year line) の
    base。``n`` は fact-row count。``organism_normalized`` / ``in_chip_atlas`` も
    含めることで sidebar の全 filter (organism / year / source / chip-atlas /
    sequence_type) で WHERE 絞りができる。
    """
    sql = (
        "COPY ("
        "  SELECT f.field, s.source_system, "
        "         COALESCE(s.sequence_type, '(unknown)') AS sequence_type, "
        "         s.submission_year, "
        "         COALESCE(s.organism_normalized, '(unknown)') AS organism_normalized, "
        "         s.in_chip_atlas, f.extract_status, "
        "         COUNT(*) AS n "
        "  FROM facts f "
        "  JOIN samples s ON s.accession = f.accession "
        "    AND s.run_name = f.run_name "
        "  GROUP BY 1, 2, 3, 4, 5, 6, 7"
        f") TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    con.execute(sql)
    rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{out_path}')").fetchone()
    logger.info("wrote %s (%d rows)", out_path, int(rows[0]) if rows else 0)


def build_aggregates(parquet_dir: Path) -> None:
    """parquet_dir に 3 つの agg_*.parquet を出力する。

    入力: samples.parquet + facts.parquet (parquet_dir 配下に存在前提)。
    DuckDB の in-process connection で集計し、それぞれ別 parquet に書く。
    一時テーブルは使わず ``COPY (...) TO`` で 1 ステップ書き出し、parquet 側で
    ZSTD 圧縮する。

    samples.parquet が旧 schema (sequence_type 列なし) でも view 側で
    ``NULL::VARCHAR`` を補って動く。
    """
    samples_path = parquet_dir / "samples.parquet"
    facts_path = parquet_dir / "facts.parquet"
    if not samples_path.exists():
        raise FileNotFoundError(f"{samples_path} not found — run build-samples first")
    if not facts_path.exists():
        raise FileNotFoundError(f"{facts_path} not found — run build-facts first")

    con = duckdb.connect(database=":memory:")
    samples_cols = _parquet_columns(con, samples_path)
    if "sequence_type" in samples_cols:
        con.execute(
            f"CREATE VIEW samples AS SELECT * FROM read_parquet('{samples_path}')"
        )
    else:
        con.execute(
            "CREATE VIEW samples AS SELECT *, "
            f"NULL::VARCHAR AS sequence_type FROM read_parquet('{samples_path}')"
        )
    con.execute(f"CREATE VIEW facts AS SELECT * FROM read_parquet('{facts_path}')")

    parquet_dir.mkdir(parents=True, exist_ok=True)
    _build_agg_samples(con, parquet_dir / "agg_samples_by_dims.parquet")
    _build_agg_field_term_dims(con, parquet_dir / "agg_field_term_dims.parquet")
    _build_agg_field_status(con, parquet_dir / "agg_field_status_dims.parquet")

    # sanity: agg_field_status の n 合計が facts の row 数と一致するか。
    n_facts = con.execute(
        "SELECT COUNT(*) FROM read_parquet(?)", [str(facts_path)]
    ).fetchone()
    n_agg = con.execute(
        "SELECT SUM(n) FROM read_parquet(?)",
        [str(parquet_dir / "agg_field_status_dims.parquet")],
    ).fetchone()
    if n_facts and n_agg and int(n_facts[0]) != int(n_agg[0]):
        logger.warning(
            "agg_field_status row sum (%d) != facts row count (%d) — "
            "likely orphan facts (no matching samples row)",
            int(n_agg[0]),
            int(n_facts[0]),
        )
