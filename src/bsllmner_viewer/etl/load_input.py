import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from bsllmner_viewer.etl.sources import SourceSystem
from bsllmner_viewer.etl.types import BsInputEntry


def _as_list(value: Any) -> list[Any]:
    """`Attributes.Attribute` のような「1 件のとき dict、複数のとき list」のフィールドを
    常に list として返す。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value

    return [value]


def _parse_iso_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _extract_bioproject(attributes: dict[str, Any] | None) -> str | None:
    if not attributes:
        return None
    for attr in _as_list(attributes.get("Attribute")):
        if attr.get("attribute_name") == "bioproject_id":
            content = attr.get("content")
            if isinstance(content, str):
                return content

    return None


def _normalize_entry(raw: dict[str, Any], wrapped: bool) -> BsInputEntry | None:
    """raw JSON 行 → BsInputEntry。accession が無い行は None を返す（呼び出し側で skip）。"""
    bs = raw["BioSample"] if wrapped else raw
    accession = bs.get("accession")
    if not isinstance(accession, str):
        return None

    description = bs.get("Description") or {}
    organism_obj = description.get("Organism") or {}
    organism = organism_obj.get("OrganismName") if isinstance(organism_obj, dict) else None
    title = description.get("Title")

    return BsInputEntry(
        accession=accession,
        publication_date=_parse_iso_datetime(bs.get("publication_date")),
        organism=organism if isinstance(organism, str) else None,
        title=title if isinstance(title, str) else None,
        bioproject=_extract_bioproject(bs.get("Attributes")),
    )


def iter_bs_entries(path: Path, source: SourceSystem) -> Iterator[BsInputEntry]:
    """input JSONL を 1 行ずつ読んで BsInputEntry を yield。

    chip-atlas / rnaseq の構造差は `source.input_is_wrapped` で吸収する。
    """
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            entry = _normalize_entry(raw, wrapped=source.input_is_wrapped)
            if entry is not None:
                yield entry
