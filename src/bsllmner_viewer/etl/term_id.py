import re

_PREFIX_TO_SOURCE: dict[str, str] = {
    "CVCL:": "Cellosaurus",
    "CL:": "CL",
    "UBERON:": "UBERON",
    "MONDO:": "MONDO",
    "CHEBI:": "ChEBI",
    "NCBIGene:": "NCBIGene",
}


def term_id_to_source(term_id: str | None) -> str | None:
    """term_id の prefix から ontology_source を導出する。

    docs/data-model.md「ontology_source の導出規則」と一対一。該当 prefix が無い場合は None。
    """
    if not term_id:
        return None
    for prefix, source in _PREFIX_TO_SOURCE.items():
        if term_id.startswith(prefix):
            return source

    return None


_IRI_OBO = re.compile(r"^http://purl\.obolibrary\.org/obo/([A-Z]+)_(.+)$")
_IRI_CELLOSAURUS = re.compile(
    r"^http://purl\.obolibrary\.org/obo/Cellosaurus#(CVCL)_(.+)$"
)


def iri_to_term_id(iri: str | None) -> str | None:
    """OWL の URI を docs/data-model.md の「URI → term_id」表に従って正規化する。"""
    if not iri:
        return None
    m = _IRI_CELLOSAURUS.match(iri)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    m = _IRI_OBO.match(iri)
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    return None
