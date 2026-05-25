import pytest

from bsllmner_viewer.etl.term_id import iri_to_term_id, term_id_to_source


@pytest.mark.parametrize(
    ("term_id", "expected"),
    [
        ("CVCL:0030", "Cellosaurus"),
        ("CL:0000001", "CL"),
        ("UBERON:0002048", "UBERON"),
        ("MONDO:0005061", "MONDO"),
        ("CHEBI:42637", "ChEBI"),
        ("NCBIGene:7157", "NCBIGene"),
        ("UNKNOWN:1", None),
        (None, None),
        ("", None),
    ],
)
def test_term_id_to_source(term_id: str | None, expected: str | None) -> None:
    assert term_id_to_source(term_id) == expected


@pytest.mark.parametrize(
    ("iri", "expected"),
    [
        ("http://purl.obolibrary.org/obo/MONDO_0005061", "MONDO:0005061"),
        ("http://purl.obolibrary.org/obo/UBERON_0002048", "UBERON:0002048"),
        ("http://purl.obolibrary.org/obo/CL_0000001", "CL:0000001"),
        ("http://purl.obolibrary.org/obo/CHEBI_42637", "CHEBI:42637"),
        ("http://purl.obolibrary.org/obo/Cellosaurus#CVCL_0030", "CVCL:0030"),
        ("http://example.com/term", None),
        (None, None),
    ],
)
def test_iri_to_term_id(iri: str | None, expected: str | None) -> None:
    assert iri_to_term_id(iri) == expected
