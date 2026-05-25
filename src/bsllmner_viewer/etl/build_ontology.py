import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from owlready2 import ThingClass, get_ontology

from bsllmner_viewer.etl.term_id import iri_to_term_id

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        pa.field("term_id", pa.string(), nullable=False),
        pa.field("ontology_source", pa.string(), nullable=False),
        pa.field("label", pa.string(), nullable=True),
        pa.field("parent_term_id", pa.string(), nullable=False),
        pa.field("depth", pa.int32(), nullable=False),
    ]
)


_ONTOLOGY_FILES: dict[str, tuple[str, ...]] = {
    "Cellosaurus": ("cellosaurus.owl",),
    "CL": ("cl_human_subset.owl", "cl_mouse_subset.owl"),
    "UBERON": ("uberon_human_subset.owl", "uberon_mouse_subset.owl"),
    "MONDO": ("mondo_human_subset.owl",),
    "ChEBI": ("chebi_subset.owl",),
}


def _collect_classes(
    files: Iterable[Path],
) -> tuple[dict[str, str | None], dict[str, set[str]]]:
    """OWL ファイル群を load → (term_id → label, term_id → direct parents) を返す。

    複数 OWL を union するときは、後勝ちで label を補完、parents は set union する。
    """
    labels: dict[str, str | None] = {}
    parents: dict[str, set[str]] = defaultdict(set)
    for path in files:
        logger.info("loading OWL: %s", path)
        onto = get_ontology(f"file://{path.resolve()}").load()
        for cls in onto.classes():
            term_id = iri_to_term_id(cls.iri)
            if not term_id:
                continue
            label = cls.label.first() if cls.label else None
            if labels.get(term_id) is None and label is not None:
                labels[term_id] = label
            elif term_id not in labels:
                labels[term_id] = None
            for parent_cls in cls.is_a:
                if not isinstance(parent_cls, ThingClass):
                    continue
                parent_id = iri_to_term_id(parent_cls.iri)
                if parent_id and parent_id != term_id:
                    parents[term_id].add(parent_id)

    return labels, parents


def _compute_depths(
    parents: dict[str, set[str]], all_terms: set[str]
) -> dict[str, int]:
    """各 term の depth を BFS で求める。root は depth=0。複数 root の場合は min(distance)。

    children map (reverse of parents) を作って top-down BFS。
    """
    children: dict[str, set[str]] = defaultdict(set)
    for term, ps in parents.items():
        for parent in ps:
            children[parent].add(term)
    roots = [t for t in all_terms if not parents.get(t)]
    depth: dict[str, int] = {r: 0 for r in roots}
    queue: deque[str] = deque(roots)
    while queue:
        node = queue.popleft()
        d = depth[node]
        for child in children.get(node, ()):
            new_d = d + 1
            if child not in depth or depth[child] > new_d:
                depth[child] = new_d
                queue.append(child)
    for term in all_terms:
        depth.setdefault(term, 0)

    return depth


def _all_ancestors_with_self(term: str, parents: dict[str, set[str]]) -> set[str]:
    """term の祖先 + self を集合で返す（transitive closure + self loop）。"""
    seen = {term}
    queue: deque[str] = deque([term])
    while queue:
        node = queue.popleft()
        for parent in parents.get(node, ()):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)

    return seen


def _build_rows_for_source(
    source: str, ontology_dir: Path
) -> list[dict[str, object]]:
    files = [ontology_dir / name for name in _ONTOLOGY_FILES[source]]
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"missing OWL file(s) for {source}: {', '.join(missing)}"
        )
    labels, parents = _collect_classes(files)
    all_terms = set(labels.keys())
    depths = _compute_depths(parents, all_terms)
    rows: list[dict[str, object]] = []
    for term in all_terms:
        ancestors = _all_ancestors_with_self(term, parents)
        for ancestor in ancestors:
            rows.append(
                {
                    "term_id": term,
                    "ontology_source": source,
                    "label": labels.get(term),
                    "parent_term_id": ancestor,
                    "depth": depths.get(term, 0),
                }
            )
    logger.info("source=%s: %d terms, %d rows", source, len(all_terms), len(rows))

    return rows


def build_ontology(
    ontology_dir: Path, out_path: Path, sources: tuple[str, ...] | None = None
) -> None:
    target = set(sources) if sources else set(_ONTOLOGY_FILES.keys())
    rows: list[dict[str, object]] = []
    for source in _ONTOLOGY_FILES:
        if source not in target:
            continue
        rows.extend(_build_rows_for_source(source, ontology_dir))

    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d ontology rows to %s", len(rows), out_path)
