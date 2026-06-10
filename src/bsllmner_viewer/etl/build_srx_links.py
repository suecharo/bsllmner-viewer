"""NCBI `SRA_Accessions.tab` を読み、samples.parquet にある BioSample に紐づく
SRA Experiment (SRX) の link を `srx_links.parquet` に出力する。さらに
samples.parquet を読み返し、accession 単位で SRX を集約した `srx_first` /
`srx_count` / `sequence_type` 列を埋めて in-place で書き戻す (Cohort 画面の
メイン table が samples 単独 SELECT で "first SRX + N more" + per-BS の
sequence_type を表示できるようにするため)。

ChIP-Atlas の ``experimentList.tab`` cache (``data/cache/experimentList.tab``、
``scripts/fetch_chip_atlas_experiment_list.py`` で download) が存在すれば
per-SRX に ``sequence_type`` 列も入れる。cache 不在時は seq_type なしで
処理し、samples.parquet 側は build-samples が入れた default を残す。

仕様: docs/data-model.md `srx_links.parquet` / `samples.sequence_type` 節 を参照。
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_chip_atlas import iter_chip_atlas_experiments
from bsllmner_viewer.etl.load_sra_accessions import iter_sra_accessions
from bsllmner_viewer.etl.seq_type import combine_seq_types, normalize_seq_type

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
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

_PROGRESS_INTERVAL = 5_000_000


def _read_target_accessions(samples_path: Path) -> set[str]:
    table = pq.read_table(samples_path, columns=["accession"])
    return set(table.column("accession").to_pylist())


def _read_srx_to_seq_type(experiment_list_tab: Path | None) -> dict[str, str]:
    """ChIP-Atlas experimentList.tab cache から SRX → sequence_type の map を作る。

    cache が無い / 引数が None なら空 dict を返す (= seq_type 列はすべて None)。
    `Annotation tracks` 等の SRX 以外の行は load_chip_atlas 側で skip 済みの
    前提。normalize_seq_type が None を返した行 (NA や未知 raw 値) は map に
    含めない。
    """
    if experiment_list_tab is None or not experiment_list_tab.exists():
        return {}
    logger.info("loading ChIP-Atlas experimentList.tab from %s", experiment_list_tab)
    out: dict[str, str] = {}
    for exp in iter_chip_atlas_experiments(experiment_list_tab):
        seq_type = normalize_seq_type(exp.track_type_class)
        if seq_type is None:
            continue
        # 1 SRX が複数行に現れる ChIP-Atlas の運用 (assembly 違いで重複) では
        # track_type_class は同じはずなので、最初の hit を採用する。
        out.setdefault(exp.srx, seq_type)
    logger.info("loaded %d SRX → sequence_type mappings", len(out))
    return out


def enrich_samples_with_srx(
    samples_path: Path,
    rows: list[dict[str, object]],
) -> None:
    """Rewrite samples.parquet with srx_first / srx_count / sequence_type filled.

    The Cohort page's main table only needs ``srx_first`` (smallest SRX, used
    as the "first" SRX in the display) and ``srx_count`` (used for the
    ``+N more`` cardinality hint). The per-SRX deep-link drill-down still
    reads ``srx_links.parquet`` via accession lookup since UNNESTing a
    LIST<STRUCT> on samples turned out to be substantially slower than the
    direct ``srx_links`` join.

    ``sequence_type`` は BS ごとに紐づく全 SRX の per-SRX ``sequence_type`` を
    ``combine_seq_types`` で集約する (1 種類 → その値、2 種類以上 → ``mixed``、
    全 None → samples 側に書かれた build-samples default をそのまま残す)。
    """
    first_by_acc: dict[str, str] = {}
    count_by_acc: dict[str, int] = defaultdict(int)
    seq_types_by_acc: dict[str, set[str | None]] = defaultdict(set)
    for row in rows:
        acc = row["accession"]
        srx = row["srx"]
        if not isinstance(acc, str) or not isinstance(srx, str):
            continue
        count_by_acc[acc] += 1
        existing = first_by_acc.get(acc)
        if existing is None or srx < existing:
            first_by_acc[acc] = srx
        seq_type_raw = row.get("sequence_type")
        seq_type = seq_type_raw if isinstance(seq_type_raw, str) else None
        seq_types_by_acc[acc].add(seq_type)

    table = pq.read_table(samples_path)
    accessions = table.column("accession").to_pylist()
    existing_seq_types = (
        table.column("sequence_type").to_pylist()
        if "sequence_type" in table.schema.names
        else [None] * len(accessions)
    )

    srx_first: list[str | None] = [first_by_acc.get(acc) for acc in accessions]
    srx_count: list[int] = [count_by_acc.get(acc, 0) for acc in accessions]
    sequence_type: list[str | None] = []
    for acc, existing in zip(accessions, existing_seq_types, strict=True):
        combined = combine_seq_types(seq_types_by_acc.get(acc, set()))
        # SRX 側に seq_type が 1 つも紐づかなかった BS は build-samples が入れた
        # default (rnaseq-human: "RNA-Seq" / chip-atlas-*: None) を残す。
        sequence_type.append(combined if combined is not None else existing)

    def _attach(t: pa.Table, name: str, array: pa.Array) -> pa.Table:
        idx = t.schema.get_field_index(name)
        if idx == -1:
            return t.append_column(name, array)
        return t.set_column(idx, name, array)

    new_table = _attach(
        table, "srx_first", pa.array(srx_first, type=pa.string())
    )
    new_table = _attach(
        new_table, "srx_count", pa.array(srx_count, type=pa.int32())
    )
    new_table = _attach(
        new_table, "sequence_type", pa.array(sequence_type, type=pa.string())
    )
    # If an earlier build wrote a srx_records column (when the schema briefly
    # carried one), strip it so the file converges back to the lean schema.
    if "srx_records" in new_table.schema.names:
        new_table = new_table.drop(["srx_records"])
    # Re-establish the Cohort page's primary sort (year DESC, accession) so
    # an `enrich-only` rebuild (running this script against a samples.parquet
    # that wasn't sorted at `build-samples` time) still leaves the file sorted.
    if "submission_year" in new_table.schema.names:
        new_table = new_table.sort_by(
            [("submission_year", "descending"), ("accession", "ascending")]
        )

    tmp_path = samples_path.with_suffix(samples_path.suffix + ".tmp")
    pq.write_table(new_table, tmp_path)
    os.replace(tmp_path, samples_path)
    enriched = sum(1 for v in srx_first if v is not None)
    logger.info(
        "enriched samples.parquet: %d / %d BioSamples carry SRX inline",
        enriched,
        len(accessions),
    )


def build_srx_links(
    source_tab: Path,
    samples_path: Path,
    out_path: Path,
    experiment_list_tab: Path | None = None,
) -> None:
    if not source_tab.exists():
        raise FileNotFoundError(
            f"{source_tab} not found — run `uv run python scripts/fetch_sra_accessions.py` first"
        )
    target = _read_target_accessions(samples_path)
    logger.info("target BioSample set: %d accessions from %s", len(target), samples_path)

    srx_to_seq_type = _read_srx_to_seq_type(experiment_list_tab)
    seq_type_hits = 0

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
        seq_type = srx_to_seq_type.get(row.accession)
        if seq_type is not None:
            seq_type_hits += 1
        rows.append(
            {
                "srx": row.accession,
                "accession": row.biosample,
                "bioproject": row.bioproject,
                "sra_study": row.study,
                "sra_sample": row.sample,
                "status": row.status,
                "sequence_type": seq_type,
            }
        )

    logger.info(
        "scanned %d rows total, kept %d SRX rows (sequence_type matched: %d)",
        scanned,
        len(rows),
        seq_type_hits,
    )
    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d SRX link rows to %s", len(rows), out_path)

    enrich_samples_with_srx(samples_path, rows)
