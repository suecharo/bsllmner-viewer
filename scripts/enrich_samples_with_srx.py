"""samples.parquet の SRX 列 (`srx_first` / `srx_count` / `srx_records`) を
既存の srx_links.parquet から再構築して in-place で書き戻す軽量 script。

`build-srx-links` を最後まで通すと `SRA_Accessions.tab` を再 scan するため
時間がかかる。本 script は既存の srx_links.parquet をそのまま使うだけなので
数秒で完了し、samples.parquet の inline SRX 列だけを更新したいときに便利。

実行例:

    uv run python scripts/enrich_samples_with_srx.py

`--out-dir` で `data/parquet` 以外の場所を指す parquet ペアにも適用できる。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

import pyarrow.parquet as pq
import typer

from bsllmner_viewer.etl.build_srx_links import enrich_samples_with_srx

logger = logging.getLogger(__name__)


def _default_out_dir() -> Path:
    return Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data")) / "parquet"


def main(
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    samples_path = out_dir / "samples.parquet"
    srx_links_path = out_dir / "srx_links.parquet"
    if not samples_path.exists():
        raise typer.BadParameter(f"{samples_path} not found")
    if not srx_links_path.exists():
        raise typer.BadParameter(
            f"{srx_links_path} not found — run `build-srx-links` first"
        )
    rows = pq.read_table(srx_links_path).to_pylist()
    logger.info("loaded %d SRX rows from %s", len(rows), srx_links_path)
    enrich_samples_with_srx(samples_path, rows)


if __name__ == "__main__":
    typer.run(main)
