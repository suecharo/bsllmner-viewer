from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from bsllmner_viewer.etl.build_facts import FIELDS, _entry_rows
from bsllmner_viewer.etl.types import ResolvedValue

_extracted_value = st.one_of(
    st.none(),
    st.text(min_size=1, max_size=10),
    st.lists(st.text(min_size=1, max_size=10), min_size=0, max_size=4),
)


def _resolved_value_strategy() -> st.SearchStrategy[ResolvedValue]:
    return st.builds(
        ResolvedValue,
        value=st.text(min_size=1, max_size=10),
        term_id=st.one_of(st.none(), st.sampled_from(["CVCL:0030", "MONDO:0005061", None])),
        label=st.one_of(st.none(), st.text(min_size=0, max_size=10)),
        exact_match=st.one_of(st.none(), st.booleans()),
        reasoning=st.one_of(st.none(), st.text(min_size=0, max_size=20)),
    )


_extracted_dict = st.dictionaries(
    keys=st.sampled_from(FIELDS),
    values=_extracted_value,
    max_size=len(FIELDS),
)

_results_dict = st.dictionaries(
    keys=st.sampled_from(FIELDS),
    values=st.lists(_resolved_value_strategy(), min_size=0, max_size=3),
    max_size=len(FIELDS),
)


def _expected_field_row_count(extracted: Any, resolved: list[ResolvedValue]) -> int:
    if extracted is None:
        extracted_values: list[str] = []
    elif isinstance(extracted, list):
        extracted_values = [str(v) for v in extracted if v is not None]
    else:
        extracted_values = [str(extracted)]
    if not extracted_values and not resolved:
        return 1
    if not resolved:
        return len(extracted_values)

    return len(resolved)


@given(extracted=_extracted_dict, results=_results_dict)
def test_row_count_matches_truth_table(
    extracted: dict[str, Any], results: dict[str, list[ResolvedValue]]
) -> None:
    rows = _entry_rows("SAM_TEST", "run_test", extracted, results)
    expected_total = sum(
        _expected_field_row_count(extracted.get(field), results.get(field) or [])
        for field in FIELDS
    )
    assert len(rows) == expected_total


@given(extracted=_extracted_dict, results=_results_dict)
def test_every_row_has_required_keys(
    extracted: dict[str, Any], results: dict[str, list[ResolvedValue]]
) -> None:
    rows = _entry_rows("SAM_TEST", "run_test", extracted, results)
    required = {
        "accession",
        "run_name",
        "field",
        "value",
        "term_id",
        "label",
        "exact_match",
        "text2term_score",
        "ontology_source",
        "extract_status",
    }
    for row in rows:
        assert set(row.keys()) == required
        assert row["extract_status"] in {"ok", "extract_failed", "mapping_failed"}
