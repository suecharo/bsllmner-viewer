"""NCBI `SRA_Accessions.tab` を読み、samples.parquet にある BioSample に紐づく
SRA Experiment (SRX) の link を `srx_links.parquet` に出力する。

仕様: docs/data-model.md `srx_links.parquet` 節 / docs/etl.md `build-srx-links` 節を参照。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_sra_accessions import iter_sra_accessions

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        pa.field("srx", pa.string(), nullable=False),
        pa.field("accession", pa.string(), nullable=False),
        pa.field("bioproject", pa.string(), nullable=True),
        pa.field("sra_study", pa.string(), nullable=True),
        pa.field("sra_sample", pa.string(), nullable=True),
        pa.field("status", pa.string(), nullable=False),
    ]
)

_PROGRESS_INTERVAL = 5_000_000


def _read_target_accessions(samples_path: Path) -> set[str]:
    table = pq.read_table(samples_path, columns=["accession"])
    return set(table.column("accession").to_pylist())


def build_srx_links(
    source_tab: Path,
    samples_path: Path,
    out_path: Path,
) -> None:
    if not source_tab.exists():
        raise FileNotFoundError(
            f"{source_tab} not found — run `uv run python scripts/fetch_sra_accessions.py` first"
        )
    target = _read_target_accessions(samples_path)
    logger.info("target BioSample set: %d accessions from %s", len(target), samples_path)

    rows: list[dict[str, object]] = []
    seen_srx: set[str] = set()
    scanned = 0
    for row in iter_sra_accessions(source_tab):
        scanned += 1
        if scanned % _PROGRESS_INTERVAL == 0:
            logger.info("scanned %d rows, kept %d SRX so far", scanned, len(rows))
        if row.type != "EXPERIMENT":
            continue
        if row.biosample is None or row.biosample not in target:
            continue
        if row.accession in seen_srx:
            logger.debug("duplicate SRX %s in source, keeping first", row.accession)
            continue
        seen_srx.add(row.accession)
        rows.append(
            {
                "srx": row.accession,
                "accession": row.biosample,
                "bioproject": row.bioproject,
                "sra_study": row.study,
                "sra_sample": row.sample,
                "status": row.status,
            }
        )

    logger.info("scanned %d rows total, kept %d SRX rows", scanned, len(rows))
    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d SRX link rows to %s", len(rows), out_path)
