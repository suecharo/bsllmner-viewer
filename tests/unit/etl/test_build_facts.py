from pathlib import Path

import pytest

from bsllmner_viewer.etl.build_facts import (
    FIELDS,
    _coerce_extracted,
    _entry_rows,
    _extract_text2term_score,
)
from bsllmner_viewer.etl.load_select import iter_select_entries


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("HeLa", ["HeLa"]),
        (123, ["123"]),
        (["a", "b"], ["a", "b"]),
        (["a", None, "b"], ["a", "b"]),
        ({"nested": "dict"}, []),  # 想定外型は warning ログ + 空
    ],
)
def test_coerce_extracted(value: object, expected: list[str]) -> None:
    assert _coerce_extracted(value) == expected


@pytest.mark.parametrize(
    ("reasoning", "expected"),
    [
        ("Exact match on rdfs:label", None),
        ("text2term score: 0.87", 0.87),
        ("text2term score: 1.0 (matched synonym)", 1.0),
        (None, None),
        ("", None),
        ("text2term score: not_a_number", None),
    ],
)
def test_extract_text2term_score(reasoning: str | None, expected: float | None) -> None:
    assert _extract_text2term_score(reasoning) == expected


def _rows_by_field_and_status(rows: list[dict[str, object]]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (str(r["field"]), str(r["extract_status"]))
        out[key] = out.get(key, 0) + 1

    return out


def test_entry_rows_ok_case(fixture_dir: Path) -> None:
    entries = list(iter_select_entries(fixture_dir / "select_minimal.json"))
    rows = _entry_rows(
        entries[0].extract.accession,
        "test_run_v1",
        entries[0].extract.extracted or {},
        entries[0].results,
    )
    # cell_line だけ ok、残り 7 fields は extract_failed
    assert len(rows) == len(FIELDS)
    counts = _rows_by_field_and_status(rows)
    assert counts[("cell_line", "ok")] == 1
    for field in FIELDS:
        if field == "cell_line":
            continue
        assert counts[(field, "extract_failed")] == 1


def test_entry_rows_extract_failed(fixture_dir: Path) -> None:
    entries = list(iter_select_entries(fixture_dir / "select_minimal.json"))
    rows = _entry_rows(
        entries[1].extract.accession,
        "test_run_v1",
        entries[1].extract.extracted or {},
        entries[1].results,
    )
    # 全 fields extract_failed
    assert len(rows) == len(FIELDS)
    assert all(r["extract_status"] == "extract_failed" for r in rows)
    assert all(r["value"] is None for r in rows)


def test_entry_rows_mapping_failed_with_array(fixture_dir: Path) -> None:
    entries = list(iter_select_entries(fixture_dir / "select_minimal.json"))
    rows = _entry_rows(
        entries[2].extract.accession,
        "test_run_v1",
        entries[2].extract.extracted or {},
        entries[2].results,
    )
    counts = _rows_by_field_and_status(rows)
    # cell_line: mapping_failed x 1, drug: mapping_failed x 2, 他 6 fields extract_failed x 1
    assert counts[("cell_line", "mapping_failed")] == 1
    assert counts[("drug", "mapping_failed")] == 2
    for field in (
        "cell_type",
        "tissue",
        "disease",
        "knockout_gene",
        "knockdown_gene",
        "overexpressed_gene",
    ):
        assert counts[(field, "extract_failed")] == 1
    assert len(rows) == 1 + 2 + 6
    # drug の 2 row が両方値を保持
    drug_rows = [r for r in rows if r["field"] == "drug"]
    drug_values = sorted(str(r["value"]) for r in drug_rows)
    assert drug_values == ["unknown_drug_1", "unknown_drug_2"]
