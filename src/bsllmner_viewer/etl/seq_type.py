"""ChIP-Atlas `track_type_class` を sequence_type に正規化する純関数群。

ChIP-Atlas の `experimentList.tab` column 3 (`track_type_class`) には
以下の値が現れる (全件 grep で確認、2026 年 6 月時点):

| raw value          | 件数      | 正規化後 sequence_type |
|--------------------|-----------|------------------------|
| ATAC-Seq           | 194,683   | ATAC-Seq               |
| Histone            | 173,175   | ChIP-Seq               |
| Bisulfite-Seq      | 130,365   | Bisulfite-Seq          |
| TFs and others     | 127,935   | ChIP-Seq               |
| Input control      |  72,812   | ChIP-Seq (input)       |
| Unclassified       |  70,051   | ChIP-Seq               |
| No description     |  44,844   | ChIP-Seq               |
| RNA polymerase     |  17,915   | ChIP-Seq               |
| DNase-seq          |  12,969   | DNase-Seq              |
| Annotation tracks  |   1,075   | Annotation track       |

Histone / TFs / RNApol / Unclassified / No description はすべて ChIP-Seq の
sub-class なので、UI filter としては集約 (= "ChIP-Seq") した方が使いやすい。
Input control は同じく ChIP-Seq だが control 用 lane で per-sample 解析対象には
含めにくいので別カテゴリで残す。
"""

from __future__ import annotations

from typing import Final

# 既知 sequence_type の安定 ordering (UI multiselect の option order で使う)。
KNOWN_SEQ_TYPES: Final[tuple[str, ...]] = (
    "ChIP-Seq",
    "ChIP-Seq (input)",
    "ATAC-Seq",
    "DNase-Seq",
    "Bisulfite-Seq",
    "RNA-Seq",
    "Annotation track",
)

# 複数 SRX が異なる seq_type を持つ BS に振る sentinel。
MIXED: Final[str] = "mixed"

# 該当 SRX が無い / experimentList.tab cache が無いときの sentinel。
UNKNOWN: Final[str] = "unknown"


_CHIP_SEQ_RAW: Final[frozenset[str]] = frozenset(
    {
        "tfs and others",
        "histone",
        "rna polymerase",
        "unclassified",
        "no description",
    }
)


def normalize_seq_type(raw: str | None) -> str | None:
    """ChIP-Atlas `track_type_class` 1 件を sequence_type ラベルに正規化する。

    None / 空白文字列 / "NA" は None を返す。未知の文字列は strip 済み raw を
    そのまま返す (Curation 等で「想定外 raw 値」を可視化できるよう)。
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or s.upper() == "NA":
        return None
    key = s.lower()
    if key in _CHIP_SEQ_RAW:
        return "ChIP-Seq"
    if key == "input control":
        return "ChIP-Seq (input)"
    if key == "atac-seq":
        return "ATAC-Seq"
    if key == "dnase-seq":
        return "DNase-Seq"
    if key == "bisulfite-seq":
        return "Bisulfite-Seq"
    if key == "rna-seq":
        return "RNA-Seq"
    if key == "annotation tracks":
        return "Annotation track"
    return s


def combine_seq_types(seq_types: set[str | None] | list[str | None]) -> str | None:
    """1 BioSample に紐づく複数 SRX の sequence_type を 1 つに集約する。

    - 全件 None → None (= UNKNOWN にするかは呼び出し側で fallback 判断)
    - 非 None が 1 種類 → その値
    - 非 None が 2 種類以上 → ``"mixed"``

    ``ChIP-Seq`` と ``ChIP-Seq (input)`` は同じ実験 type の表裏なので 1 種類扱い
    にしたいが、UI filter で peak vs input を区別したいときの邪魔になるので
    PoC 範囲では文字列一致で集約する (= input control と Histone が混在する
    BS は ``mixed`` 扱い)。
    """
    non_null = {s for s in seq_types if s is not None}
    if not non_null:
        return None
    if len(non_null) == 1:
        return next(iter(non_null))
    return MIXED
