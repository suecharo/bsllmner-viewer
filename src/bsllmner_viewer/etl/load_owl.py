"""OWL を `lxml.etree.iterparse` で streaming parse するヘルパー。

owlready2 / rdflib は OWL 全体を in-memory に load するため、ChEBI フル OWL (774MB)
などで memory 消費が大きい。`lxml.iterparse` なら `<owl:Class>` 要素単位で順次処理
→ `clear()` + preceding sibling 削除で memory を解放できる。

bsllmner-viewer の `build_ontology` は subset OWL から term_id + label、フル OWL から
`rdfs:subClassOf` を抜くだけで足りるので、抽出関数も 2 つだけ用意する。
"""

from collections.abc import Iterator
from pathlib import Path

from lxml import etree

from bsllmner_viewer.etl.term_id import iri_to_term_id

_NS_OWL = "http://www.w3.org/2002/07/owl#"
_NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_NS_RDFS = "http://www.w3.org/2000/01/rdf-schema#"

_CLASS_TAG = f"{{{_NS_OWL}}}Class"
_LABEL_TAG = f"{{{_NS_RDFS}}}label"
_SUBCLASS_TAG = f"{{{_NS_RDFS}}}subClassOf"
_RDF_ABOUT = f"{{{_NS_RDF}}}about"
_RDF_RESOURCE = f"{{{_NS_RDF}}}resource"


def _iter_class_elements(path: Path) -> Iterator[etree._Element]:
    """OWL を streaming して `<owl:Class>` 要素を 1 つずつ yield する。

    yield 直後に処理済み要素 + preceding sibling を削除して memory を解放するのが
    iterparse の定石 (https://lxml.de/parsing.html#modifying-the-tree)。
    """
    context = etree.iterparse(
        str(path), events=("end",), tag=_CLASS_TAG, huge_tree=True
    )
    for _, elem in context:
        yield elem
        elem.clear(keep_tail=False)
        parent = elem.getparent()
        if parent is not None:
            while elem.getprevious() is not None:
                del parent[0]
    del context


def iter_class_labels(path: Path) -> Iterator[tuple[str, str | None]]:
    """OWL から `(term_id, label)` を 1 件ずつ yield する。

    `rdf:about` の IRI を `iri_to_term_id` で正規化できない (= 対応 ontology の
    prefix を持たない) Class は skip。`rdfs:label` 要素が無い Class は label=None。
    """
    for elem in _iter_class_elements(path):
        term_id = iri_to_term_id(elem.get(_RDF_ABOUT))
        if not term_id:
            continue
        label_elem = elem.find(_LABEL_TAG)
        label = label_elem.text if label_elem is not None else None
        yield term_id, label


def iter_subclass_edges(path: Path) -> Iterator[tuple[str, str]]:
    """OWL から `(child_term_id, parent_term_id)` を 1 件ずつ yield する。

    `rdf:resource` 属性で直接 IRI を参照している subClassOf のみ採用。blank node や
    `<owl:Restriction>` を子要素に持つ subClassOf は無視する (`rdf:resource` 属性が
    無いので自然に skip される)。self-loop も除外。
    """
    for elem in _iter_class_elements(path):
        child_id = iri_to_term_id(elem.get(_RDF_ABOUT))
        if not child_id:
            continue
        for sub in elem.iterchildren(_SUBCLASS_TAG):
            parent_id = iri_to_term_id(sub.get(_RDF_RESOURCE))
            if not parent_id or parent_id == child_id:
                continue
            yield child_id, parent_id
