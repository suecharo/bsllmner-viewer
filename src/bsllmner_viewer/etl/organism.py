"""`samples.organism` の表記揺れを正規化する。

実 data (chip-atlas-hg38 / chip-atlas-mm10 / rnaseq-human) で出現した raw 値を、
表記揺れ map + xenograft / mixed / hybrid の特殊判定で集約する。未知の表記は raw を
そのまま返し、UI / Curation 側で「想定外 organism」として可視化できるようにする。

正規化ルールの仕様は docs/data-model.md「organism_normalized の正規化ルール」を SSOT
とし、本 module はその表通り 1:1 で実装する。
"""

_EXACT_LOWER: dict[str, str] = {
    "homo sapiens": "Homo sapiens",
    "homo sapien": "Homo sapiens",
    "human": "Homo sapiens",
    "mus musculus": "Mus musculus",
    "mouse": "Mus musculus",
    "mus musculus domesticus": "Mus musculus",
    "mus musculus musculus": "Mus musculus",
    "mus musculus castaneus": "Mus musculus",
}


def normalize_organism(raw: str | None) -> str | None:
    """raw organism 文字列 → 正規化済 organism。

    None / 空白のみは None を返す (呼び出し側で系統 default に fallback)。
    docs/data-model.md「organism_normalized の正規化ルール」と一対一。
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    if "xenograft" in lower:
        return "xenograft"
    if "mixed" in lower:
        return "mixed"
    if " x " in lower and "mus" in lower:
        return "Mus musculus hybrid"

    return _EXACT_LOWER.get(lower, stripped)
