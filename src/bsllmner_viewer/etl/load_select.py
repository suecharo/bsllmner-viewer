from collections.abc import Iterator
from pathlib import Path

import ijson

from bsllmner_viewer.etl.types import RunMetadata, SelectEntry


def iter_select_entries(path: Path) -> Iterator[SelectEntry]:
    """SelectResult.entries[] を streaming で 1 件ずつ yield する。

    `select_*.json` は 1GB 超の単一 JSON があり得るので、ijson で逐次 parse する。
    """
    with path.open("rb") as f:
        for raw in ijson.items(f, "entries.item"):
            yield SelectEntry.model_validate(raw)


def read_run_metadata(path: Path) -> RunMetadata:
    """SelectResult.run_metadata だけを読む。"""
    with path.open("rb") as f:
        for key, value in ijson.kvitems(f, ""):
            if key == "run_metadata":
                return RunMetadata.model_validate(value)
    raise ValueError(f"run_metadata not found in {path}")


def read_error_count(path: Path) -> int:
    """SelectResult.errors の件数だけを読む。"""
    with path.open("rb") as f:
        count = 0
        for _ in ijson.items(f, "errors.item"):
            count += 1

        return count
