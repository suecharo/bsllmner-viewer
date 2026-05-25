import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_select import read_error_count, read_run_metadata
from bsllmner_viewer.etl.sources import SOURCE_SYSTEMS, SourceSystem, iter_result_files
from bsllmner_viewer.etl.types import SourceSystemId

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("source_system", pa.string(), nullable=False),
        pa.field("model", pa.string(), nullable=False),
        pa.field("start_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("end_time", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("status", pa.string(), nullable=False),
        pa.field("total_entries", pa.int32(), nullable=False),
        pa.field("error_count", pa.int32(), nullable=False),
        pa.field("processing_time_sec", pa.float64(), nullable=True),
    ]
)


def _build_row(result_file: Path, source: SourceSystem) -> dict[str, object]:
    metadata = read_run_metadata(result_file)
    error_count = read_error_count(result_file)

    return {
        "run_name": metadata.run_name,
        "source_system": source.id,
        "model": metadata.model,
        "start_time": metadata.start_time,
        "end_time": metadata.end_time,
        "status": metadata.status,
        "total_entries": metadata.total_entries or 0,
        "error_count": error_count,
        "processing_time_sec": metadata.processing_time_sec,
    }


def build_runs(
    data_dir: Path, out_path: Path, source_systems: tuple[SourceSystemId, ...] | None = None
) -> None:
    target_ids = set(source_systems) if source_systems else None
    rows: list[dict[str, object]] = []
    for source in SOURCE_SYSTEMS:
        if target_ids is not None and source.id not in target_ids:
            continue
        for result_file in iter_result_files(data_dir, source):
            logger.info("reading run_metadata: %s", result_file)
            rows.append(_build_row(result_file, source))

    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d run rows to %s", len(rows), out_path)
