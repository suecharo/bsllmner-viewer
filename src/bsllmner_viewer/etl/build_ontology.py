import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from bsllmner_viewer.etl.load_owl import iter_class_labels, iter_subclass_edges

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


# 各 ontology_source ごとの (subset OWL = term_id + label の source) と
# (hierarchy OWL = rdfs:subClassOf の source) ペア。Cellosaurus は subset を
# 持たないので両方 cellosaurus.owl を指す (全体を subset 扱い)。
# docs/data-model.md「対象 ontology と source ファイル」と一対一対応する。
_ONTOLOGY_SOURCES: dict[str, tuple[tuple[str, ...], str]] = {
    "Cellosaurus": (("cellosaurus.owl",), "cellosaurus.owl"),
    "CL": (("cl_human_subset.owl", "cl_mouse_subset.owl"), "cl.owl"),
    "UBERON": (
        ("uberon_human_subset.owl", "uberon_mouse_subset.owl"),
        "uberon.owl",
    ),
    "MONDO": (("mondo_human_subset.owl",), "mondo.owl"),
    "ChEBI": (("chebi_subset.owl",), "chebi.owl"),
}


def _collect_subset(files: Iterable[Path]) -> dict[str, str | None]:
    """subset OWL 群から term_id → label (None 可) を集める。

    同じ term_id が複数 subset に出る場合は、先に出た label を残しつつ None を
    後から非 None で上書き補完する。
    """
    labels: dict[str, str | None] = {}
    for path in files:
        logger.info("loading subset OWL: %s", path)
        for term_id, label in iter_class_labels(path):
            if term_id not in labels or (labels[term_id] is None and label is not None):
                labels[term_id] = label

    return labels


def _collect_hierarchy(
    path: Path, allowed_terms: set[str]
) -> dict[str, set[str]]:
    """hierarchy OWL の rdfs:subClassOf から、child / parent ともに `allowed_terms`
    に含まれるペアのみを採用した parent map を返す。
    """
    parents: dict[str, set[str]] = defaultdict(set)
    logger.info("loading hierarchy OWL: %s", path)
    for child, parent in iter_subclass_edges(path):
        if child in allowed_terms and parent in allowed_terms:
            parents[child].add(parent)

    return parents


def _compute_depths(
    parents: dict[str, set[str]], all_terms: set[str]
) -> dict[str, int]:
    """各 term の depth を root からの BFS 最短距離として求める。複数 root なら最小値。

    呼び出し側で parent map は `all_terms` 内に restrict 済みである前提
    (`_collect_hierarchy` が child / parent 両方を filter しているため成立)。
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


def _ancestors_with_self(term: str, parents: dict[str, set[str]]) -> set[str]:
    """`term` の祖先 + self を集合で返す (transitive closure + self loop 用 self)。"""
    seen = {term}
    queue: deque[str] = deque([term])
    while queue:
        node = queue.popleft()
        for parent in parents.get(node, ()):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)

    return seen


def build_source_rows(
    source: str, labels: dict[str, str | None], parents: dict[str, set[str]]
) -> list[dict[str, object]]:
    """1 ontology_source 分の row 群を組み立てる (parquet 書き出し前段)。

    test から `_collect_subset` / `_collect_hierarchy` を経ずに直接呼べるよう
    public にする (subset 外 restrict のテスト用)。
    """
    all_terms = set(labels.keys())
    depths = _compute_depths(parents, all_terms)
    rows: list[dict[str, object]] = []
    for term in sorted(all_terms):
        depth = depths[term]
        label = labels[term]
        for ancestor in _ancestors_with_self(term, parents):
            rows.append(
                {
                    "term_id": term,
                    "ontology_source": source,
                    "label": label,
                    "parent_term_id": ancestor,
                    "depth": depth,
                }
            )

    return rows


def build_ontology(
    ontology_dir: Path, out_path: Path, sources: tuple[str, ...] | None = None
) -> None:
    target = set(sources) if sources else set(_ONTOLOGY_SOURCES.keys())
    rows: list[dict[str, object]] = []
    for source, (subset_names, hierarchy_name) in _ONTOLOGY_SOURCES.items():
        if source not in target:
            continue
        subset_files = [ontology_dir / name for name in subset_names]
        hierarchy_file = ontology_dir / hierarchy_name
        missing = [
            str(p) for p in [*subset_files, hierarchy_file] if not p.exists()
        ]
        if missing:
            raise FileNotFoundError(
                f"missing OWL file(s) for {source}: {', '.join(missing)}"
            )
        labels = _collect_subset(subset_files)
        parents = _collect_hierarchy(hierarchy_file, set(labels.keys()))
        source_rows = build_source_rows(source, labels, parents)
        logger.info(
            "source=%s: %d terms, %d edges, %d rows",
            source,
            len(labels),
            sum(len(ps) for ps in parents.values()),
            len(source_rows),
        )
        rows.extend(source_rows)

    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    logger.info("wrote %d ontology rows to %s", len(rows), out_path)
