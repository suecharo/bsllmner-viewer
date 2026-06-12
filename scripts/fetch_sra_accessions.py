"""NCBI SRA_Accessions.tab を `${BSLLMNER_VIEWER_DATA_DIR}/cache/` に download する。

事前 download 用の script。ETL の `build-srx-links` は download せず、本 script の
出力 (`SRA_Accessions.tab`) を読むだけ。32 GB の download を ETL に持ち込まないため。

skip 判定は size + ETag / Last-Modified の二段構え (``_http_cache.py`` 経由)。
途中で中断された場合は ``.partial`` から HTTP Range で resume する。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from _http_cache import download_with_resume

logger = logging.getLogger("fetch_sra_accessions")

URL = "https://ftp.ncbi.nlm.nih.gov/sra/reports/Metadata/SRA_Accessions.tab"


def _default_cache_path() -> Path:
    data_dir = Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data"))
    return data_dir / "cache" / "SRA_Accessions.tab"


def fetch_sra_accessions(out: Path, force: bool = False) -> Path:
    return download_with_resume(
        URL,
        out,
        force=force,
        progress_interval=1024**3,
        unit_divisor=1024**3,
        unit_label="GiB",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_default_cache_path(),
        help="Output path (default: $BSLLMNER_VIEWER_DATA_DIR/cache/SRA_Accessions.tab)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if size + etag already match the remote",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch_sra_accessions(args.out, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
