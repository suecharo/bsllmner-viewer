from pathlib import Path

from bsllmner_viewer.etl.load_owl import iter_class_labels, iter_subclass_edges


def test_iter_class_labels_yields_term_id_and_label(fixture_dir: Path) -> None:
    labels = dict(iter_class_labels(fixture_dir / "mondo_tiny.owl"))

    # 期待: MONDO 6 件 (1〜6)。例外 (example.org の Class) は iri_to_term_id が
    # None を返すので含まれない。
    assert set(labels) == {
        "MONDO:0000001",
        "MONDO:0000002",
        "MONDO:0000003",
        "MONDO:0000004",
        "MONDO:0000005",
        "MONDO:0000006",
    }
    assert labels["MONDO:0000001"] == "disease A"
    assert labels["MONDO:0000002"] == "disease B"
    # label 要素がない Class は None
    assert labels["MONDO:0000003"] is None
    assert labels["MONDO:0000004"] == "disease D"


def test_iter_subclass_edges_yields_direct_rdf_resource_edges(
    fixture_dir: Path,
) -> None:
    edges = list(iter_subclass_edges(fixture_dir / "mondo_tiny.owl"))

    # 期待される edges (順序不問):
    # - MONDO:2 → MONDO:1
    # - MONDO:3 → MONDO:1, MONDO:3 → MONDO:2 (多親)
    # - MONDO:4 → MONDO:1 (self-loop は除外)
    # - MONDO:5 → MONDO:1 (blank node の subClassOf は skip、rdf:resource ありだけ採用)
    # - MONDO:6 → なし (owl:Restriction だけは skip)
    # - example.org の Class は iri_to_term_id が None で skip
    assert set(edges) == {
        ("MONDO:0000002", "MONDO:0000001"),
        ("MONDO:0000003", "MONDO:0000001"),
        ("MONDO:0000003", "MONDO:0000002"),
        ("MONDO:0000004", "MONDO:0000001"),
        ("MONDO:0000005", "MONDO:0000001"),
    }


def test_iter_subclass_edges_drops_self_loop(fixture_dir: Path) -> None:
    edges = list(iter_subclass_edges(fixture_dir / "mondo_tiny.owl"))
    assert all(child != parent for child, parent in edges)
