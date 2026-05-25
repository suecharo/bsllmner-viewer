import pytest

from bsllmner_viewer.etl.organism import normalize_organism


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Homo sapiens 表記揺れ (実 data 全 5 種)
        ("Homo sapiens", "Homo sapiens"),
        ("homo sapiens", "Homo sapiens"),
        ("Homo Sapiens", "Homo sapiens"),
        ("Human", "Homo sapiens"),
        ("human", "Homo sapiens"),
        # Mus musculus 表記揺れ
        ("Mus musculus", "Mus musculus"),
        ("mus musculus", "Mus musculus"),
        ("Mus Musculus", "Mus musculus"),
        ("Mouse", "Mus musculus"),
        ("mouse", "Mus musculus"),
        # 亜種は species level に折り畳む
        ("Mus musculus domesticus", "Mus musculus"),
        ("Mus musculus musculus", "Mus musculus"),
        ("Mus musculus castaneus", "Mus musculus"),
        # xenograft (substring match、case-insensitive)
        ("Homo sapiens/Mus musculus xenograft", "xenograft"),
        ("Xenograft", "xenograft"),
        # mixed (substring match)
        ("mixed sample", "mixed"),
        ("Mixed Sample", "mixed"),
        # hybrid (' x ' を含む + mus を含む)
        ("Mus musculus x Mus spretus", "Mus musculus hybrid"),
        ("Mus musculus musculus x Mus musculus castaneus", "Mus musculus hybrid"),
    ],
)
def test_normalize_known_variants(raw: str, expected: str) -> None:
    assert normalize_organism(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\t"])
def test_normalize_empty_returns_none(raw: str | None) -> None:
    assert normalize_organism(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        # 想定外の表記は strip 済み raw をそのまま返す (UI で気付けるように)
        "Drosophila melanogaster",
        "Rattus norvegicus",
        # leading / trailing space は trim される
    ],
)
def test_normalize_unknown_returns_stripped_raw(raw: str) -> None:
    assert normalize_organism(raw) == raw.strip()
    assert normalize_organism(f"  {raw}  ") == raw.strip()


def test_normalize_xenograft_takes_precedence_over_exact_match() -> None:
    # 'Homo sapiens' は xenograft 文字列を含むと xenograft 扱いになる
    assert normalize_organism("Homo sapiens xenograft model") == "xenograft"


def test_normalize_hybrid_does_not_misfire_on_unrelated_x() -> None:
    # ' x ' を含むが mus を含まない → hybrid にならない (raw 返却)
    assert normalize_organism("Cross x Reference") == "Cross x Reference"
