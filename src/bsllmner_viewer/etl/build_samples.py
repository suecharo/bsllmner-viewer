import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_input import iter_bs_entries
from bsllmner_viewer.etl.load_select import iter_select_entries, read_run_metadata
from bsllmner_viewer.etl.organism import normalize_organism
from bsllmner_viewer.etl.sources import SOURCE_SYSTEMS, SourceSystem, iter_run_pairs
from bsllmner_viewer.etl.types import BsInputEntry, SourceSystemId

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        pa.field("accession", pa.string(), nullable=False),
        pa.field("organism", pa.string(), nullable=True),
        pa.field("organism_normalized", pa.string(), nullable=True),
        pa.field("submission_year", pa.int32(), nullable=True),
        pa.field("project", pa.string(), nullable=True),
        pa.field("title", pa.string(), nullable=True),
        pa.field("source_system", pa.string(), nullable=False),
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("in_chip_atlas", pa.bool_(), nullable=False),
        pa.field("chip_atlas_genome", pa.string(), nullable=True),
        pa.field("chip_atlas_srx_count", pa.int32(), nullable=False),
    ]
)


def _read_input_map(input_files: list[Path], source: SourceSystem) -> dict[str, BsInputEntry]:
    out: dict[str, BsInputEntry] = {}
    for input_file in input_files:
        for entry in iter_bs_entries(input_file, source):
            out[entry.accession] = entry

    return out


def _read_result_accessions(result_file: Path) -> set[str]:
    return {entry.extract.accession for entry in iter_select_entries(result_file)}


def _make_row(
    accession: str,
    source: SourceSystem,
    run_name: str,
    bs: BsInputEntry | None,
) -> dict[str, object]:
    raw_organism = bs.organism if bs else None
    return {
        "accession": accession,
        "organism": raw_organism if raw_organism else source.organism,
        "organism_normalized": normalize_organism(raw_organism) or source.organism,
        "submission_year": (
            bs.publication_date.year if bs and bs.publication_date else None
        ),
        "project": bs.bioproject if bs else None,
        "title": bs.title if bs else None,
        "source_system": source.id,
        "run_name": run_name,
        "in_chip_atlas": source.in_chip_atlas,
        "chip_atlas_genome": source.chip_atlas_genome,
        "chip_atlas_srx_count": 0,
    }


def build_samples(
    data_dir: Path, out_path: Path, source_systems: tuple[SourceSystemId, ...] | None = None
) -> None:
    target_ids = set(source_systems) if source_systems else None
    rows: list[dict[str, object]] = []
    for source in SOURCE_SYSTEMS:
        if target_ids is not None and source.id not in target_ids:
            continue
        for input_files, result_file in iter_run_pairs(data_dir, source):
            logger.info("processing %s (input=%d files)", result_file, len(input_files))
            run_name = read_run_metadata(result_file).run_name
            accession_set = _read_result_accessions(result_file)
            input_map = _read_input_map(input_files, source)
            missing = 0
            for accession in accession_set:
                bs = input_map.get(accession)
                if bs is None:
                    missing += 1
                rows.append(_make_row(accession, source, run_name, bs))
            if missing:
                logger.warning(
                    "%s: %d accession(s) in result not found in input JSONL",
                    result_file.name,
                    missing,
                )

    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d sample rows to %s", len(rows), out_path)
