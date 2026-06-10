from __future__ import annotations

import pytest

from bsllmner_viewer.etl.seq_type import (
    KNOWN_SEQ_TYPES,
    MIXED,
    combine_seq_types,
    normalize_seq_type,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ATAC-Seq", "ATAC-Seq"),
        ("atac-seq", "ATAC-Seq"),
        ("ATAC-SEQ", "ATAC-Seq"),
        ("DNase-seq", "DNase-Seq"),
        ("dnase-seq", "DNase-Seq"),
        ("Bisulfite-Seq", "Bisulfite-Seq"),
        ("bisulfite-seq", "Bisulfite-Seq"),
        ("TFs and others", "ChIP-Seq"),
        ("tfs and others", "ChIP-Seq"),
        ("Histone", "ChIP-Seq"),
        ("RNA polymerase", "ChIP-Seq"),
        ("Unclassified", "ChIP-Seq"),
        ("No description", "ChIP-Seq"),
        ("Input control", "ChIP-Seq (input)"),
        ("RNA-Seq", "RNA-Seq"),
        ("Annotation tracks", "Annotation track"),
    ],
)
def test_normalize_seq_type_known(raw: str, expected: str) -> None:
    assert normalize_seq_type(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "NA", "na", "Na"])
def test_normalize_seq_type_treats_empty_as_none(raw: str | None) -> None:
    assert normalize_seq_type(raw) is None


def test_normalize_seq_type_unknown_passes_through_stripped() -> None:
    # 未知 raw 値は strip 済みで return される (Curation で表記揺れを可視化する)。
    assert normalize_seq_type("  Cool-Seq (variant)  ") == "Cool-Seq (variant)"


def test_normalize_seq_type_all_known_targets_in_known_set() -> None:
    for label in (
        "ChIP-Seq",
        "ChIP-Seq (input)",
        "ATAC-Seq",
        "DNase-Seq",
        "Bisulfite-Seq",
        "RNA-Seq",
        "Annotation track",
    ):
        assert label in KNOWN_SEQ_TYPES


def test_combine_seq_types_empty_returns_none() -> None:
    assert combine_seq_types([]) is None
    assert combine_seq_types([None]) is None
    assert combine_seq_types([None, None]) is None


def test_combine_seq_types_single_non_null() -> None:
    assert combine_seq_types(["ChIP-Seq"]) == "ChIP-Seq"
    assert combine_seq_types(["ChIP-Seq", None]) == "ChIP-Seq"
    assert combine_seq_types(["ATAC-Seq", "ATAC-Seq", "ATAC-Seq"]) == "ATAC-Seq"


def test_combine_seq_types_mixed_returns_sentinel() -> None:
    assert combine_seq_types(["ChIP-Seq", "ATAC-Seq"]) == MIXED
    # input control も別文字列扱いなので mixed
    assert combine_seq_types(["ChIP-Seq", "ChIP-Seq (input)"]) == MIXED
