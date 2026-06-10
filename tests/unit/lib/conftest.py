from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_ONTOLOGY_SCHEMA = pa.schema(
    [
        pa.field("term_id", pa.string(), nullable=False),
        pa.field("ontology_source", pa.string(), nullable=False),
        pa.field("label", pa.string(), nullable=True),
        pa.field("parent_term_id", pa.string(), nullable=False),
        pa.field("depth", pa.int32(), nullable=False),
    ]
)

# Fixture topology:
#
# TEST source (a tiny tree with transitive closure + self-loops):
#   T:1 (root, depth=0)
#   ├── T:2 (depth=1)
#   │   └── T:3 (depth=2)
#   └── T:4 (depth=1)
#
# TEST2 source: a single root U:1 (depth=0).
#
# CELL source: Cellosaurus-style — only self-loop for C:1 (depth=0).
_ROWS: list[tuple[str, str, str, str, int]] = [
    ("T:1", "TEST", "root", "T:1", 0),
    ("T:2", "TEST", "child", "T:1", 1),
    ("T:2", "TEST", "child", "T:2", 1),
    ("T:3", "TEST", "grandchild", "T:2", 2),
    ("T:3", "TEST", "grandchild", "T:1", 2),
    ("T:3", "TEST", "grandchild", "T:3", 2),
    ("T:4", "TEST", "other-child", "T:1", 1),
    ("T:4", "TEST", "other-child", "T:4", 1),
    ("U:1", "TEST2", "u-root", "U:1", 0),
    ("C:1", "CELL", "c1", "C:1", 0),
    # MONDO hierarchy used by roll-up tests (matches FIELD_TO_ONTOLOGY['disease']):
    #   MONDO:1 'neoplasm' (depth=0, root)
    #     └── MONDO:10 'breast neoplasm' (depth=1)
    #     └── MONDO:11 'lung neoplasm' (depth=1)
    #   MONDO:2 'diabetes' (depth=0, root)
    ("MONDO:1", "MONDO", "neoplasm", "MONDO:1", 0),
    ("MONDO:2", "MONDO", "diabetes", "MONDO:2", 0),
    ("MONDO:10", "MONDO", "breast neoplasm", "MONDO:1", 1),
    ("MONDO:10", "MONDO", "breast neoplasm", "MONDO:10", 1),
    ("MONDO:11", "MONDO", "lung neoplasm", "MONDO:1", 1),
    ("MONDO:11", "MONDO", "lung neoplasm", "MONDO:11", 1),
]


@pytest.fixture()
def fixture_parquet_dir(tmp_path: Path) -> Path:
    pdir = tmp_path / "parquet"
    pdir.mkdir()
    rows = [
        {
            "term_id": term_id,
            "ontology_source": source,
            "label": label_text,
            "parent_term_id": parent_id,
            "depth": depth,
        }
        for term_id, source, label_text, parent_id, depth in _ROWS
    ]
    table = pa.Table.from_pylist(rows, schema=_ONTOLOGY_SCHEMA)
    pq.write_table(table, pdir / "ontology.parquet")
    return pdir


# ---- samples + facts fixture for aggregation tests ----

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


_SAMPLES_ROWS = [
    # 最後の列が sequence_type。A1/A2 を ChIP-Seq、A4/A5 を ATAC-Seq、A3 を RNA-Seq
    # にして、aggregation tests で seq filter の通り道が確認できるようにしてある。
    # source_system は chip-atlas-* / rnaseq-human を直接使い、overlay 軸
    # (s.source_system LIKE 'chip-atlas-%') の secondary_count を実物どおりに
    # 検証できるようにする。
    ("A1", "Homo sapiens", "Homo sapiens", 2024, "PRJ1", "Sample A1",
     "chip-atlas-hg38", "run1", "ChIP-Seq"),
    ("A2", "Homo sapiens", "Homo sapiens", 2024, "PRJ1", "Sample A2",
     "chip-atlas-hg38", "run1", "ChIP-Seq"),
    ("A3", "Mus musculus", "Mus musculus", 2025, "PRJ2", "Sample A3",
     "rnaseq-human", "run2", "RNA-Seq"),
    # A4 / A5 carry leaf MONDO terms one level below the root, used by
    # the depth=0 roll-up tests.
    ("A4", "Homo sapiens", "Homo sapiens", 2024, "PRJ1", "Sample A4",
     "chip-atlas-hg38", "run1", "ATAC-Seq"),
    ("A5", "Homo sapiens", "Homo sapiens", 2024, "PRJ1", "Sample A5",
     "chip-atlas-hg38", "run1", "ATAC-Seq"),
]

_FACTS_ROWS = [
    # A1: disease=MONDO:1, drug=CHEBI:1
    ("A1", "run1", "disease", "cancer",  "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("A1", "run1", "drug",    "aspirin", "CHEBI:1", "aspirin",  True, 1.0, "ChEBI", "ok"),
    # A2: disease=MONDO:1, drug=CHEBI:2
    ("A2", "run1", "disease", "cancer",  "MONDO:1", "neoplasm", True, 1.0, "MONDO", "ok"),
    ("A2", "run1", "drug",    "ibuprofen", "CHEBI:2", "ibuprofen", True, 1.0, "ChEBI", "ok"),
    # A3: disease=MONDO:2, no drug
    ("A3", "run2", "disease", "diabetes", "MONDO:2", "diabetes", True, 1.0, "MONDO", "ok"),
    # A4: disease=MONDO:10 (leaf, child of MONDO:1)
    ("A4", "run1", "disease", "breast cancer", "MONDO:10",
     "breast neoplasm", True, 1.0, "MONDO", "ok"),
    # A5: disease=MONDO:11 (leaf, child of MONDO:1)
    ("A5", "run1", "disease", "lung cancer", "MONDO:11",
     "lung neoplasm", True, 1.0, "MONDO", "ok"),
    # --- Quality-meta rows: exercise mapping_failed / extract_failed paths for
    # Home F3/F4 and Curation D1/D2/D3 without changing the term_id
    # distribution that other tests assert against.
    # tissue mapping_failed on A1 + A4: same raw value "weird tissue X"
    # appears twice, so D3 surfaces a frequency > 1.
    ("A1", "run1", "tissue", "weird tissue X", None, None, None, None,
     None, "mapping_failed"),
    ("A4", "run1", "tissue", "weird tissue X", None, None, None, None,
     None, "mapping_failed"),
    # tissue extract_failed on A2 (value=None means LLM didn't extract).
    ("A2", "run1", "tissue", None, None, None, None, None, None,
     "extract_failed"),
    # drug mapping_failed on A3 (raw value but unmapped).
    ("A3", "run2", "drug", "herbal compound", None, None, None, None,
     None, "mapping_failed"),
]


_SRX_LINKS_SCHEMA = pa.schema(
    [
        pa.field("srx", pa.string(), nullable=False),
        pa.field("accession", pa.string(), nullable=False),
        pa.field("bioproject", pa.string(), nullable=True),
        pa.field("sra_study", pa.string(), nullable=True),
        pa.field("sra_sample", pa.string(), nullable=True),
        pa.field("status", pa.string(), nullable=False),
        pa.field("sequence_type", pa.string(), nullable=True),
    ]
)

# Per-BioSample SRX topology used by per-SRX expansion + cohort_samples tests:
#   A1 -> 2 SRX (multi)
#   A2 -> 1 SRX
#   A3 -> 1 SRX (rnaseq-human source — non-ChIP-Atlas BS)
#   A4 -> 0 SRX  (covers "BS without any SRX" path; INNER JOIN drops it)
#   A5 -> 3 SRX (multi, with mixed status to exercise status-passthrough)
_SRX_ROWS = [
    ("SRX1", "A1", "PRJ1", "SRP1", "SRS1", "live", "ChIP-Seq"),
    ("SRX2", "A1", "PRJ1", "SRP1", "SRS1", "live", "ChIP-Seq"),
    ("SRX3", "A2", "PRJ1", "SRP1", "SRS2", "live", "ChIP-Seq"),
    ("SRX4", "A3", "PRJ2", "SRP2", "SRS3", "live", "RNA-Seq"),
    ("SRX5", "A5", "PRJ1", "SRP1", "SRS5", "live", "ATAC-Seq"),
    ("SRX6", "A5", "PRJ1", "SRP1", "SRS5", "suppressed", "ATAC-Seq"),
    ("SRX7", "A5", "PRJ1", "SRP1", "SRS5", "live", "ATAC-Seq"),
]


@pytest.fixture()
def aggregation_parquet_dir(fixture_parquet_dir: Path) -> Path:
    pdir = fixture_parquet_dir

    # Aggregate _SRX_ROWS per accession so the inline scalar columns on
    # samples.parquet (`srx_first` = MIN(srx), `srx_count` = COUNT) stay
    # consistent with the standalone srx_links.parquet fixture.
    srx_first_by_acc: dict[str, str] = {}
    srx_count_by_acc: dict[str, int] = {}
    for srx, acc, _bp, _study, _sample, _status, _seq in _SRX_ROWS:
        srx_count_by_acc[acc] = srx_count_by_acc.get(acc, 0) + 1
        existing = srx_first_by_acc.get(acc)
        if existing is None or srx < existing:
            srx_first_by_acc[acc] = srx

    base_fields = [
        f.name for f in _SAMPLES_SCHEMA
        if f.name not in {"srx_first", "srx_count"}
    ]
    sample_rows: list[dict[str, object]] = []
    for row in _SAMPLES_ROWS:
        d: dict[str, object] = dict(zip(base_fields, row, strict=True))
        acc = str(d["accession"])
        d["srx_first"] = srx_first_by_acc.get(acc)
        d["srx_count"] = srx_count_by_acc.get(acc, 0)
        sample_rows.append(d)
    samples = pa.Table.from_pylist(sample_rows, schema=_SAMPLES_SCHEMA)
    pq.write_table(samples, pdir / "samples.parquet")

    facts = pa.Table.from_pylist(
        [
            dict(zip([f.name for f in _FACTS_SCHEMA], r, strict=True))
            for r in _FACTS_ROWS
        ],
        schema=_FACTS_SCHEMA,
    )
    pq.write_table(facts, pdir / "facts.parquet")

    srx_links = pa.Table.from_pylist(
        [
            dict(zip([f.name for f in _SRX_LINKS_SCHEMA], r, strict=True))
            for r in _SRX_ROWS
        ],
        schema=_SRX_LINKS_SCHEMA,
    )
    pq.write_table(srx_links, pdir / "srx_links.parquet")

    return pdir
