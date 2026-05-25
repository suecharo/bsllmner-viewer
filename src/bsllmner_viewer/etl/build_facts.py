import logging
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_select import iter_select_entries, read_run_metadata
from bsllmner_viewer.etl.sources import SOURCE_SYSTEMS, iter_result_files
from bsllmner_viewer.etl.term_id import term_id_to_source
from bsllmner_viewer.etl.types import ResolvedValue, SourceSystemId

logger = logging.getLogger(__name__)

FIELDS: tuple[str, ...] = (
    "cell_line",
    "cell_type",
    "tissue",
    "disease",
    "drug",
    "knockout_gene",
    "knockdown_gene",
    "overexpressed_gene",
)

_TEXT2TERM_SCORE = re.compile(r"text2term score:\s*([\d.]+)")

_SCHEMA = pa.schema(
    [
        pa.field("accession", pa.string(), nullable=False),
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("field", pa.string(), nullable=False),
        pa.field("value", pa.string(), nullable=True),
        pa.field("term_id", pa.string(), nullable=True),
        pa.field("label", pa.string(), nullable=True),
        pa.field("exact_match", pa.bool_(), nullable=True),
        pa.field("text2term_score", pa.float32(), nullable=True),
        pa.field("ontology_source", pa.string(), nullable=True),
        pa.field("extract_status", pa.string(), nullable=False),
    ]
)


def _coerce_extracted(value: Any) -> list[str]:
    """LLM が返した extract 値を list[str] に正規化する。

    None -> []、scalar -> [str(scalar)]、list -> 要素を str 化 (None 要素は除外)。
    型が不正な場合 (dict など) は warning を出して [] を返す。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str | int | float | bool):
        return [str(value)]
    logger.warning("unexpected extracted value type: %r", type(value))

    return []


def _extract_text2term_score(reasoning: str | None) -> float | None:
    if not reasoning:
        return None
    m = _TEXT2TERM_SCORE.search(reasoning)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _row_extract_failed(accession: str, run_name: str, field: str) -> dict[str, object]:
    return {
        "accession": accession,
        "run_name": run_name,
        "field": field,
        "value": None,
        "term_id": None,
        "label": None,
        "exact_match": None,
        "text2term_score": None,
        "ontology_source": None,
        "extract_status": "extract_failed",
    }


def _row_mapping_failed(
    accession: str, run_name: str, field: str, value: str
) -> dict[str, object]:
    return {
        "accession": accession,
        "run_name": run_name,
        "field": field,
        "value": value,
        "term_id": None,
        "label": None,
        "exact_match": None,
        "text2term_score": None,
        "ontology_source": None,
        "extract_status": "mapping_failed",
    }


def _row_ok(
    accession: str, run_name: str, field: str, rv: ResolvedValue
) -> dict[str, object]:
    return {
        "accession": accession,
        "run_name": run_name,
        "field": field,
        "value": rv.value,
        "term_id": rv.term_id,
        "label": rv.label,
        "exact_match": rv.exact_match,
        "text2term_score": _extract_text2term_score(rv.reasoning),
        "ontology_source": term_id_to_source(rv.term_id),
        "extract_status": "ok",
    }


def _entry_rows(
    accession: str,
    run_name: str,
    extracted: dict[str, Any],
    results: dict[str, list[ResolvedValue]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for field in FIELDS:
        extracted_values = _coerce_extracted(extracted.get(field))
        resolved = results.get(field) or []
        if not extracted_values and not resolved:
            rows.append(_row_extract_failed(accession, run_name, field))
        elif not resolved:
            for value in extracted_values:
                rows.append(_row_mapping_failed(accession, run_name, field, value))
        else:
            for rv in resolved:
                rows.append(_row_ok(accession, run_name, field, rv))

    return rows


def build_facts(
    data_dir: Path, out_path: Path, source_systems: tuple[SourceSystemId, ...] | None = None
) -> None:
    target_ids = set(source_systems) if source_systems else None
    rows: list[dict[str, object]] = []
    for source in SOURCE_SYSTEMS:
        if target_ids is not None and source.id not in target_ids:
            continue
        for result_file in iter_result_files(data_dir, source):
            logger.info("building facts from %s", result_file)
            run_name = read_run_metadata(result_file).run_name
            for entry in iter_select_entries(result_file):
                extracted = entry.extract.extracted or {}
                rows.extend(
                    _entry_rows(entry.extract.accession, run_name, extracted, entry.results)
                )

    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d fact rows to %s", len(rows), out_path)
