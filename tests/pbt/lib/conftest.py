"""Self-contained fixture for the fast/live parity PBT.

We don't share the unit fixture because pytest can't load a sibling
``conftest.py`` as a plugin (no top-level ``tests`` package). The dataset is
intentionally a superset of the unit fixture — a few NULL dim rows in
particular — so the parity invariant also covers ``UNKNOWN`` sentinel paths.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bsllmner_viewer.etl.build_aggregates import build_aggregates

_SAMPLES_SCHEMA = pa.schema(
    [
        pa.field("accession", pa.string(), nullable=False),
        pa.field("organism", pa.string(), nullable=True),
        pa.field("organism_normalized", pa.string(), nullable=True),
        pa.field("submission_year", pa.int32(), nullable=True),
        pa.field("project", pa.string(), nullable=True),
        pa.field("title", pa.string(), nullable=True),
        pa.field("source_system", pa.string(), nullable=False),
        pa.field("run_name", pa.string(), nullable=False),
        pa.field("sequence_type", pa.string(), nullable=True),
        pa.field("srx_first", pa.string(), nullable=True),
        pa.field("srx_count", pa.int32(), nullable=False),
    ]
)

_FACTS_SCHEMA = pa.schema(
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

# B1〜B7 covers the cross product of (organism × source × seq_type × year)
# the strategy draws from, plus one NULL-bearing row for each nullable dim
# so the UNKNOWN sentinel path is exercised on both the live and the agg
# side.
_SAMPLES = [
    ("B1", "Homo sapiens", "Homo sapiens", 2024, None, None,
     "chip-atlas-hg38", "run1", "ChIP-Seq", None, 0),
    ("B2", "Homo sapiens", "Homo sapiens", 2025, None, None,
     "chip-atlas-hg38", "run1", "ATAC-Seq", None, 0),
    ("B3", "Mus musculus", "Mus musculus", 2024, None, None,
     "rnaseq-human", "run2", "RNA-Seq", None, 0),
    ("B4", "Mus musculus", "Mus musculus", 2025, None, None,
     "rnaseq-human", "run2", "RNA-Seq", None, 0),
    # NULL organism_normalized: live must include this row when UNKNOWN is in
    # the filter set, ditto the agg side which stores it as '(unknown)'.
    ("B5", None, None, 2024, None, None,
     "chip-atlas-hg38", "run1", "ChIP-Seq", None, 0),
    # NULL sequence_type: same parity invariant for the sequence_type dim.
    ("B6", "Homo sapiens", "Homo sapiens", 2025, None, None,
     "chip-atlas-hg38", "run1", None, None, 0),
    # NULL submission_year: live should drop this row when year_min/max is
    # set (because the WHERE adds a >= / <= bound), and the agg side does
    # too because submission_year remains NULL in agg_*.
    ("B7", "Homo sapiens", "Homo sapiens", None, None, None,
     "chip-atlas-hg38", "run1", "ChIP-Seq", None, 0),
]

_FACTS = [
    ("B1", "run1", "disease", "cancer", "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("B1", "run1", "drug", "aspirin", "CHEBI:1", "aspirin", True, 1.0, "ChEBI", "ok"),
    ("B2", "run1", "disease", "cancer", "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("B3", "run2", "disease", "diabetes", "MONDO:2", "diabetes", True, 1.0, "MONDO", "ok"),
    ("B4", "run2", "drug", "ibuprofen", "CHEBI:2", "ibuprofen", True, 1.0, "ChEBI", "ok"),
    ("B5", "run1", "disease", "cancer", "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("B6", "run1", "disease", "cancer", "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("B7", "run1", "disease", "cancer", "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    # Quality-meta rows used by mapping_status_* parity (extract_failed /
    # mapping_failed are flat under filters).
    ("B2", "run1", "tissue", "tissue X", None, None, None, None, None, "mapping_failed"),
    ("B5", "run1", "tissue", None, None, None, None, None, None, "extract_failed"),
]


@pytest.fixture()
def aggregation_parquet_dir(tmp_path: Path) -> Path:
    pdir = tmp_path / "parquet"
    pdir.mkdir()
    fields = [f.name for f in _SAMPLES_SCHEMA]
    samples = pa.Table.from_pylist(
        [dict(zip(fields, row, strict=True)) for row in _SAMPLES],
        schema=_SAMPLES_SCHEMA,
    )
    pq.write_table(samples, pdir / "samples.parquet")

    facts_fields = [f.name for f in _FACTS_SCHEMA]
    facts = pa.Table.from_pylist(
        [dict(zip(facts_fields, row, strict=True)) for row in _FACTS],
        schema=_FACTS_SCHEMA,
    )
    pq.write_table(facts, pdir / "facts.parquet")
    build_aggregates(pdir)
    return pdir
